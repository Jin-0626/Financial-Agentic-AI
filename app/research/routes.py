from app.research import protocol
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.responses import StreamingResponse
from app.research.schemas import ChatRequest, ChatHistoryResponse, ThreadInfo
from app.infrastructure.diagnostics import sanitize_error
from app.infrastructure.auth import authenticated_identity, organization, user
from app.research import history
from app.research import messages
from app.research import resources
from app.research import service
from app.research import streaming

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authenticated_identity)])


@router.post("/chat")
async def chat(body: ChatRequest, identity=Depends(authenticated_identity)):
    if not resources.resources.agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    try:
        data = body.model_dump()
        if identity is not None:
            data.update(org_id=identity.org, user_id=identity.user)
        message = data.get("message", "")
        if not message.strip() and data.get("resume") is None:
            raise HTTPException(400, "Message content cannot be empty")
        thread_id = data.get("thread_id", "default-thread")
        user_id = data.get("user_id", "local-user")
        org_id = data.get("org_id", "default-org")
        thread_id = messages._owned_thread(thread_id, org_id, user_id)
        response_schema = messages._request_schema(data)

        result = await asyncio.wait_for(
            service._chat(
                message,
                thread_id,
                user_id,
                org_id,
                response_schema,
                data.get("resume"),
                data.get("terminal_context"),
            ),
            timeout=120.0,
        )

        return result
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Request timed out, please try again later")
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=sanitize_error(e))


@router.post("/files/upload")
@router.post("/chat-with-file")
@router.post("/chat-with-file/stream")
async def retired_upload():
    raise HTTPException(
        status_code=410,
        detail="Uploaded-file analysis has been retired. Use provider-based financial research through /api/chat or /api/chat/stream.",
    )


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, identity=Depends(authenticated_identity)):
    if not resources.resources.agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    data = body.model_dump()
    if identity is not None:
        data.update(org_id=identity.org, user_id=identity.user)
    message = data.get("message", "")
    thread_id = data.get("thread_id", "default-thread")
    user_id = data.get("user_id", "local-user")
    org_id = data.get("org_id", "default-org")
    thread_id = messages._owned_thread(thread_id, org_id, user_id)
    response_schema = messages._request_schema(data)
    if not message.strip() and data.get("resume") is None:
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    return StreamingResponse(
        streaming._stream_sse(
            message,
            thread_id,
            user_id,
            org_id,
            response_schema=response_schema,
            resume=data.get("resume"),
            terminal_context=data.get("terminal_context"),
        ),
        media_type="text/event-stream",
        headers=protocol._stream_headers(),
    )


@router.post("/sandbox/execute")
@router.post("/execute-code")
@router.get("/sandbox/download")
@router.get("/sandbox/status")
async def retired_sandbox():
    raise HTTPException(
        status_code=410,
        detail="Sandbox and arbitrary code execution are retired. Use provider-based research.",
    )


@router.get("/history/{thread_id}", response_model=ChatHistoryResponse)
async def get_history(
    thread_id: str, org_id: str = Depends(organization), user_id: str = Depends(user)
):
    return await history._get_history(messages._owned_thread(thread_id, org_id, user_id))


@router.get("/threads", response_model=list[ThreadInfo])
async def list_threads(org_id: str = Depends(organization), user_id: str = Depends(user)):
    return await history._list_threads(org_id, user_id)


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: str, org_id: str = Depends(organization), user_id: str = Depends(user)
):
    await history._delete_thread(messages._owned_thread(thread_id, org_id, user_id))
    return {"status": "ok"}


@router.get("/traces")
async def list_traces(
    response: Response, org_id: str = Depends(organization), user_id: str = Depends(user)
):
    response.headers["Cache-Control"] = "no-store"
    return {"traces": resources.traces.read(org_id, user_id)}


@router.get("/traces/{trace_id}")
async def trace_detail(
    trace_id: str,
    response: Response,
    org_id: str = Depends(organization),
    user_id: str = Depends(user),
):
    response.headers["Cache-Control"] = "no-store"
    rows = resources.traces.read(org_id, user_id, trace_id)
    if not rows:
        raise HTTPException(404, "Trace not found in this workspace")
    return rows[0]
