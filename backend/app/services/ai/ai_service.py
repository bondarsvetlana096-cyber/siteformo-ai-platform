import os

from openai import OpenAI

from app.services.ai.lead_extractor import extract_lead_data
from app.services.ai.prompts import SYSTEM_PROMPT
from app.services.memory.redis_memory import get_history, save_turn
from app.services.state.fsm import detect_next_state, get_state, set_state

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


async def generate_ai_reply(user_text: str, user_id: str, channel: str) -> str:
    user_text = (user_text or "").strip()
    if not user_text:
        return "Напишите, пожалуйста, какую услугу вы ищете."

    current_state = get_state(user_id)
    history = get_history(user_id)

    lead_data = await extract_lead_data(user_text, user_id=user_id, channel=channel)
    next_state = detect_next_state(user_text, lead_data)
    set_state(user_id, next_state)

    system_prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Канал: {channel}\n"
        f"Текущий этап воронки: {current_state}\n"
        f"Следующий предполагаемый этап: {next_state}\n"
        f"Извлечённые данные лида: {lead_data}\n"
    )

    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        input=[
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": user_text},
        ],
    )

    reply = (response.output_text or "").strip()
    if not reply:
        reply = "Понял. Уточните, пожалуйста, город и какая услуга нужна?"

    save_turn(user_id, user_text, reply)
    return reply
