"""The question-answering pipeline, built as a small LangGraph.

    START -> guard -> retrieve --(found evidence?)--> generate -> validate -> END
                                \\--(nothing found)--> no_evidence --------> END

    guard        reject empty or attack-like questions (guardrails.check_input)
    retrieve     embed the question, search Pinecone as this user, keep hits above min_relevance
    generate     ask the chat model to answer from that evidence only
    validate     block answers that leak secrets (guardrails.check_output); keep only the cited sources
    no_evidence  fixed reply when nothing authorized and relevant was found; the model is not called

`chat(principal, question, history)` is the one function the web app calls.
"""
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.aws import answer, embed
from app.config import settings
from app.guardrails import check_input, check_output
from app.observability import trace
from app.vector import search


class State(TypedDict, total=False):
    """The data passed from step to step. Each step returns only the keys it changes."""
    question: str
    principal: Any      # who is asking: .tenant, .groups, .subject
    passages: list      # Pinecone hits that passed the relevance cut-off
    answer: str
    sources: list       # citation info shown to the user
    status: str         # 'answered' or 'no_evidence'
    history: list       # earlier [{'role', 'content'}] turns of this conversation


@trace("guard")
def guard(s):
    return {'question': check_input(s['question'])}


@trace("retrieve")
def retrieve(s):
    p = s['principal']
    # Short follow-ups such as "summarize that" mean nothing alone, so prepend the last user question.
    prior_questions = [m['content'] for m in s.get('history', []) if m['role'] == 'user']
    query = (prior_questions[-1] + '\nFollow-up: ' + s['question']) if prior_questions else s['question']

    hits = search(p.tenant, embed(query), p.groups, p.subject)
    hits = [h for h in hits if h['score'] >= settings().min_relevance]
    sources = [{'index': i + 1,
                'document_id': h['metadata'].get('document_id', ''),
                'title': h['metadata'].get('title', ''),
                'page': h['metadata'].get('page', 1),
                'excerpt': h['metadata'].get('text', '')[:320]}
               for i, h in enumerate(hits)]
    return {'passages': hits, 'sources': sources}


def route(s):
    """Decides the next step after retrieve."""
    return 'generate' if s['passages'] else 'no_evidence'


@trace("no_evidence")
def no_evidence(s):
    return {'answer': 'I could not find supporting evidence in documents you are authorized to access.',
            'status': 'no_evidence'}


@trace("generate")
def generate(s):
    # Numbered so the model can cite [1], [2]; the numbers match `sources`.
    evidence = '\n\n'.join(
        f'[{i + 1}] {h["metadata"].get("title", "Document")}, page {h["metadata"].get("page", 1)}: '
        f'{h["metadata"].get("text", "")}'
        for i, h in enumerate(s['passages']))
    return {'answer': answer(s['question'], evidence, s.get('history', [])), 'status': 'answered'}


def only_cited(answer, sources):
    """Keep only the sources the answer cites, e.g. [1] or [1, 3]. Numbers are left as they were.

    If the answer cites nothing we can match, keep every source, so evidence is never hidden
    just because the model formatted its citations differently.
    """
    cited = {int(n) for group in re.findall(r'\[(\d+(?:\s*,\s*\d+)*)\]', answer)
             for n in re.split(r'\s*,\s*', group)}
    return [src for src in sources if src['index'] in cited] or sources


@trace("validate")
def validate(s):
    checked = check_output(s['answer'])
    return {'answer': checked, 'sources': only_cited(checked, s.get('sources', []))}


def make_graph():
    g = StateGraph(State)
    for name, fn in [('guard', guard), ('retrieve', retrieve), ('no_evidence', no_evidence),
                     ('generate', generate), ('validate', validate)]:
        g.add_node(name, fn)
    g.add_edge(START, 'guard')
    g.add_edge('guard', 'retrieve')
    g.add_conditional_edges('retrieve', route, {'generate': 'generate', 'no_evidence': 'no_evidence'})
    g.add_edge('generate', 'validate')
    g.add_edge('validate', END)
    g.add_edge('no_evidence', END)
    return g.compile()


graph = make_graph()


@trace("chat")
def chat(p, question, history=None):
    r = graph.invoke({'principal': p, 'question': question, 'history': history or []})
    return {'answer': r['answer'], 'sources': r.get('sources', []), 'status': r.get('status', 'answered')}
