import os
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
from google import genai
from google.genai import types

app = FastAPI()

ai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

SYSTEM_INSTRUCTION = """
You are a study and research assistant.
Provide structured, concise answers for:
1. CA Final subjects (FR, AFM, Auditing, Direct/Indirect Tax).
2. Public policy, constitutional law, and political economy.
Format cleanly for WhatsApp.
"""

@app.get("/")
def health_check():
    return {"status": "running"}

@app.post("/whatsapp")
async def reply(Body: str = Form(""), From: str = Form("")):
    user_text = Body.strip()
    twiml = MessagingResponse()

    if not user_text:
        twiml.message("Please send a valid message.")
        return Response(content=str(twiml), media_type="application/xml")

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3,
                max_output_tokens=800
            )
        )
        answer = response.text.strip()
    except Exception as e:
        answer = f"Error: {str(e)}"

    if len(answer) > 1500:
        answer = answer[:1450] + "\n\n...[Truncated]"

    twiml.message(answer)
    return Response(content=str(twiml), media_type="application/xml")
