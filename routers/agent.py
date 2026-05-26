from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
import models
from services.auth import get_current_user
from services.agent_core import run_agent_loop

router = APIRouter(prefix="/api/agent", tags=["agent"])

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

@router.post("/chat")
def chat_with_agent(request: ChatRequest, user: models.User = Depends(get_current_user)):
    try:
        messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]
        response = run_agent_loop(messages, user_id=user.id)
        return {"reply": response.get("content", "我没有生成有效回复，请再试一次。")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent failed: {str(e)}")
