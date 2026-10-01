from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, Literal, Sequence, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from app.config import (
    MAX_SUB_QUESTIONS,
    OPENAI_API_KEY,
    RAG_MAX_RETRIES,
    RETRIEVAL_K,
    SUB_QUESTION_WORKERS,
)
from app.mcp_tools import get_mcp_tools, mcp_tools_available
from app.vector_store import get_vector_store, hybrid_search
from app.retrieval_cache import get_cached_retrieval, cache_retrieval


# One pool per process, shared by every request thread, so total sub-question
# concurrency stays capped no matter how many chat requests arrive at once.
_SUB_QUESTION_POOL = ThreadPoolExecutor(
    max_workers=SUB_QUESTION_WORKERS, thread_name_prefix="subq"
)


class RAGState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    context: str
    validation: str
    validation_feedback: str
    retry_count: int
    is_complex: bool
    plan: str
    sub_questions: list[str]
    sub_answers: list[str]


def _llm() -> ChatOpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return ChatOpenAI(model="gpt-4o-mini", temperature=0)


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts).strip()
    return str(content)


def _last_user_text(messages: Sequence[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return _message_text(m)
    return ""


def _last_ai_message(messages: Sequence[BaseMessage]) -> AIMessage | None:
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return m
    return None


def _format_retrieved_docs(docs: list) -> str:
    blocks = []
    for i, d in enumerate(docs, start=1):
        src = d.metadata.get("source", f"doc_{i}")
        blocks.append(f"[{i}] (source: {src})\n{d.page_content}")
    return "\n\n---\n\n".join(blocks)


def _tool_results_text(messages: Sequence[BaseMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            parts.append(_message_text(message))
    return "\n\n---\n\n".join(parts)


def _combined_context(state: RAGState) -> str:
    context = state.get("context") or ""
    tool_results = _tool_results_text(state["messages"])
    if not tool_results:
        return context
    web_block = f"Web search results:\n{tool_results}"
    if context:
        return f"{context}\n\n---\n\n{web_block}"
    return web_block


def _generate_system_prompt(*, context: str, with_web_search: bool) -> str:
    base = (
        "You are a precise assistant for a retrieval-augmented system. "
        "Answer using the provided knowledge base context when it is relevant. "
    )
    if with_web_search:
        base += (
            "You also have web search tools (MCP). "
            "Use them when the knowledge base is empty, outdated, or insufficient, "
            "or when the question needs current public information. "
            "Prefer the knowledge base when it already answers the question. "
            "Cite web sources when you use search results. "
        )
    base += (
        "Do not invent facts. "
        "If neither the knowledge base nor web search provides enough information, say so clearly.\n\n"
        f"Knowledge base context:\n{context or '(empty)'}"
    )
    return base


def _search_context(query: str) -> str:
    query = query.strip()
    if not query:
        return ""

    cached_docs = get_cached_retrieval(query)
    if cached_docs is not None:
        return _format_retrieved_docs(cached_docs)

    docs = hybrid_search(query, k=RETRIEVAL_K)
    if not docs:
        return ""

    cache_retrieval(query, docs)
    return _format_retrieved_docs(docs)

def _search_context_raw(query: str, k: int | None = None) -> list:
    """Same retrieval as _search_context, but returns raw Document objects
    (with .metadata / .page_content intact) instead of a formatted string.
    Used by eval scripts to score Recall@k / NDCG against golden chunk ids.
    """
    query = query.strip()
    if not query:
        return []
    return hybrid_search(query, k=k or RETRIEVAL_K)


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError("plan response must be a JSON object")
    return data


def _parse_plan_response(text: str) -> tuple[bool, str, list[str]]:
    try:
        data = _extract_json_object(text)
    except (json.JSONDecodeError, ValueError):
        return False, "", []

    complexity = str(data.get("complexity", "simple")).strip().lower()
    is_complex = complexity == "complex"
    plan = str(data.get("plan", "") or "").strip()

    raw_sub_questions = data.get("sub_questions") or []
    sub_questions: list[str] = []
    if isinstance(raw_sub_questions, list):
        for item in raw_sub_questions:
            q = str(item).strip()
            if q:
                sub_questions.append(q)

    if is_complex and not sub_questions:
        is_complex = False
    return is_complex, plan, sub_questions


def plan(state: RAGState) -> dict:
    question = _last_user_text(state["messages"])
    if not question.strip():
        return {"is_complex": False, "plan": "", "sub_questions": []}

    response = _llm().bind(response_format={"type": "json_object"}).invoke(
        [
            SystemMessage(
                content=(
                    "You analyze user questions for a retrieval-augmented assistant.\n"
                    "Decide whether the question is simple or complex.\n\n"
                    "simple: one focused question answerable with a single retrieval.\n"
                    "complex: multi-part, comparative, sequential, or requires "
                    "combining several distinct facts or sub-topics.\n\n"
                    "Reply with JSON only, using this schema:\n"
                    "{\n"
                    '  "complexity": "simple" | "complex",\n'
                    '  "plan": "one short sentence; empty string when simple",\n'
                    '  "sub_questions": ["..."]\n'
                    "}\n\n"
                    "For complex questions, provide 2-5 sub_questions, each "
                    "independently searchable. For simple questions, use an empty "
                    "plan and an empty sub_questions array."
                )
            ),
            HumanMessage(content=f"Question:\n{question}"),
        ]
    )
    is_complex, plan_text, sub_questions = _parse_plan_response(_message_text(response))
    return {
        "is_complex": is_complex,
        "plan": plan_text,
        "sub_questions": sub_questions,
    }


def _generate_sub_answer(sub_question: str, context: str) -> str:
    system = SystemMessage(
        content=(
            "You are a precise assistant for a retrieval-augmented system. "
            "Answer the sub-question using ONLY the provided context. "
            "If the context is empty or insufficient, say you do not have enough "
            "information in the knowledge base and avoid inventing facts.\n\n"
            f"Context:\n{context or '(empty)'}"
        )
    )
    response = _llm().invoke([system, HumanMessage(content=sub_question)])
    return _message_text(response)


def execute(state: RAGState) -> dict:
    sub_questions = state.get("sub_questions") or []
    if not sub_questions:
        return {"context": "", "sub_answers": []}

    sub_questions = sub_questions[:MAX_SUB_QUESTIONS]

    def _run(sub_q: str) -> tuple[str, str]:
        context = _search_context(sub_q)
        return context, _generate_sub_answer(sub_q, context)

    # Sub-questions are independent and mostly wait on network I/O (OpenSearch,
    # OpenAI), so they run concurrently. Results keep the original order.
    futures = [_SUB_QUESTION_POOL.submit(_run, q) for q in sub_questions]
    results = [f.result() for f in futures]

    sub_answers: list[str] = []
    sections: list[str] = []
    for idx, (sub_q, (context, answer)) in enumerate(zip(sub_questions, results), start=1):
        sub_answers.append(answer)
        sections.append(
            f"## Sub-question {idx}: {sub_q}\n\n"
            f"Context:\n{context or '(empty)'}\n\n"
            f"Answer:\n{answer}"
        )

    plan_text = state.get("plan") or ""
    header = f"Execution plan: {plan_text}\n\n" if plan_text else ""
    return {
        "sub_answers": sub_answers,
        "context": header + "\n\n---\n\n".join(sections),
    }


def merge(state: RAGState) -> dict:
    question = _last_user_text(state["messages"])
    sub_questions = state.get("sub_questions") or []
    sub_answers = state.get("sub_answers") or []
    plan_text = state.get("plan") or ""

    parts = []
    for idx, (sub_q, sub_a) in enumerate(zip(sub_questions, sub_answers), start=1):
        parts.append(f"### Sub-question {idx}: {sub_q}\n{sub_a}")
    sub_answer_block = "\n\n".join(parts)

    response = _llm().invoke(
        [
            SystemMessage(
                content=(
                    "You synthesize sub-answers into one coherent final answer "
                    "for a retrieval-augmented assistant. "
                    "Use ONLY facts present in the sub-answers. "
                    "Do not invent new information. "
                    "If sub-answers are incomplete, say so clearly."
                )
            ),
            HumanMessage(
                content=(
                    f"Original question:\n{question}\n\n"
                    f"Plan:\n{plan_text or '(none)'}\n\n"
                    f"Sub-answers:\n{sub_answer_block or '(none)'}"
                )
            ),
        ]
    )
    if not isinstance(response, AIMessage):
        response = AIMessage(content=str(response.content))
    return {"messages": [response]}


def route_after_plan(state: RAGState) -> Literal["execute", "retrieve"]:
    if state.get("is_complex") and state.get("sub_questions"):
        return "execute"
    return "retrieve"


def retrieve(state: RAGState) -> dict:
    query = _last_user_text(state["messages"])
    if not query.strip():
        return {"context": ""}
    return {"context": _search_context(query)}


_generate_llm: Runnable[Any, AIMessage] | None = None


def _get_generate_llm() -> Runnable[Any, AIMessage]:
    global _generate_llm
    if _generate_llm is None:
        tools = get_mcp_tools()
        _generate_llm = _llm().bind_tools(tools) if tools else _llm()
    return _generate_llm


def generate(state: RAGState) -> dict:
    context = state.get("context") or ""
    with_web_search = mcp_tools_available()
    system = SystemMessage(
        content=_generate_system_prompt(context=context, with_web_search=with_web_search)
    )
    response = _get_generate_llm().invoke([system, *state["messages"]])
    if not isinstance(response, AIMessage):
        response = AIMessage(content=str(response.content))
    return {"messages": [response]}


def validate(state: RAGState) -> dict:
    last_ai = _last_ai_message(state["messages"])
    if last_ai is None:
        return {"validation": "fail", "validation_feedback": "No assistant answer to validate."}

    context = _combined_context(state)
    question = _last_user_text(state["messages"])
    answer = _message_text(last_ai)

    verdict = _llm().invoke(
        [
            SystemMessage(
                content=(
                    "You validate answers for a retrieval-augmented assistant. "
                    "Reply on the first line with exactly PASS or FAIL. "
                    "On the second line, give one short reason.\n\n"
                    "The answer may be grounded in the knowledge base context, "
                    "web search tool results, or both.\n\n"
                    "FAIL if the answer:\n"
                    "- invents facts not supported by the available context\n"
                    "- ignores relevant context when it exists\n"
                    "- is incomplete or does not address the question\n"
                    "- contradicts the context\n\n"
                    "PASS if the answer is grounded, complete enough, and honest "
                    "about missing context."
                )
            ),
            HumanMessage(
                content=(
                    f"Question:\n{question}\n\n"
                    f"Available context:\n{context or '(empty)'}\n\n"
                    f"Answer:\n{answer}"
                )
            ),
        ]
    )
    text = _message_text(verdict)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    head = lines[0].upper() if lines else "FAIL"
    feedback = lines[1] if len(lines) > 1 else text
    passed = head.startswith("PASS")
    return {
        "validation": "pass" if passed else "fail",
        "validation_feedback": feedback,
    }


def refine(state: RAGState) -> dict:
    context = _combined_context(state)
    feedback = state.get("validation_feedback") or "The previous answer was insufficient."
    system = SystemMessage(
        content=(
            "You are revising a retrieval-augmented assistant answer. "
            "The previous answer failed validation. "
            "Produce a corrected, complete answer using ONLY the provided context "
            "(knowledge base and any web search results). "
            "Do not invent facts. If context is insufficient, say so clearly.\n\n"
            f"Validation feedback: {feedback}\n\n"
            f"Available context:\n{context or '(empty)'}"
        )
    )
    response = _llm().invoke([system, *state["messages"]])
    if not isinstance(response, AIMessage):
        response = AIMessage(content=str(response.content))
    return {
        "messages": [response],
        "retry_count": state.get("retry_count", 0) + 1,
    }


def route_after_validate(state: RAGState) -> Literal["end", "refine"]:
    if state.get("validation") == "pass":
        return "end"
    if state.get("retry_count", 0) >= RAG_MAX_RETRIES:
        return "end"
    return "refine"


def build_rag_graph():
    mcp_tools = get_mcp_tools()
    graph = StateGraph(RAGState)
    graph.add_node("plan", plan)
    graph.add_node("execute", execute)
    graph.add_node("merge", merge)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    if mcp_tools:
        graph.add_node("mcp_tools", ToolNode(mcp_tools))
    graph.add_node("validate", validate)
    graph.add_node("refine", refine)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {"execute": "execute", "retrieve": "retrieve"},
    )
    graph.add_edge("execute", "merge")
    graph.add_edge("merge", "validate")
    graph.add_edge("retrieve", "generate")
    if mcp_tools:
        graph.add_conditional_edges(
            "generate",
            tools_condition,
            {"tools": "mcp_tools", "__end__": "validate"},
        )
        graph.add_edge("mcp_tools", "generate")
    else:
        graph.add_edge("generate", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {"end": END, "refine": "refine"},
    )
    graph.add_edge("refine", "validate")
    return graph.compile()


_rag_app = None


def get_rag_app():
    global _rag_app
    if _rag_app is None:
        _rag_app = build_rag_graph()
    return _rag_app


def initial_rag_state(messages: Sequence[BaseMessage]) -> RAGState:
    return {
        "messages": messages,
        "context": "",
        "validation": "",
        "validation_feedback": "",
        "retry_count": 0,
        "is_complex": False,
        "plan": "",
        "sub_questions": [],
        "sub_answers": [],
    }
