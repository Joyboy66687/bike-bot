"""AI integration kept free of Telegram router registration."""
from groq import AsyncGroq


async def request_ai_answer(prompt, context, api_key, model):
    client = AsyncGroq(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Отвечай кратко и не выдумывай данные."},
            {"role": "user", "content": f"СВОДКА БАЗЫ:\n{context}\n\n{prompt}"},
        ],
    )
    return response.choices[0].message.content
