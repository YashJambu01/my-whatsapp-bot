import os
import json
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
from google import genai
from google.genai import types
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI()

# Indian Standard Time offset (UTC+5:30) using built-in standard library
IST = timezone(timedelta(hours=5, minutes=30))

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
CALENDAR_ID = os.environ.get("CALENDAR_ID", "primary")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

def get_google_services():
    if not SERVICE_ACCOUNT_JSON:
        return None, None
    try:
        info = json.loads(SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=[
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/spreadsheets"
            ]
        )
        cal = build("calendar", "v3", credentials=creds)
        sheets = build("sheets", "v4", credentials=creds)
        return cal, sheets
    except Exception:
        return None, None

def add_calendar_event(summary: str, start_datetime: str, end_datetime: str, description: str = "") -> str:
    """Adds a study session or task event to Google Calendar.
    Args:
        summary: Title of the event (e.g. 'CA Final AFM Practice').
        start_datetime: Event start in ISO format 'YYYY-MM-DDTHH:MM:SS' (e.g. '2026-09-17T16:00:00').
        end_datetime: Event end in ISO format 'YYYY-MM-DDTHH:MM:SS' (e.g. '2026-09-17T18:00:00').
        description: Notes about the session.
    """
    cal_service, _ = get_google_services()
    if not cal_service:
        return "Google Calendar service account is not yet configured in Render environment."
    try:
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": f"{start_datetime}+05:30", "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": f"{end_datetime}+05:30", "timeZone": "Asia/Kolkata"},
        }
        cal_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return f"Created Calendar Event: '{summary}' on {start_datetime}."
    except Exception as e:
        return f"Error creating event: {str(e)}"

def log_progress_to_sheet(task: str, category: str, status: str, hours_spent: float, notes: str = "") -> str:
    """Logs a completed or in-progress study/research task into the progress sheet.
    Args:
        task: Name of the task or chapter.
        category: 'CA Final' or 'Policy Research'.
        status: 'Completed', 'In Progress', or 'Pending'.
        hours_spent: Hours spent (e.g. 2.5).
        notes: Key takeaways or notes.
    """
    _, sheets_service = get_google_services()
    if not sheets_service or not SPREADSHEET_ID:
        return "Google Sheets ID or Service Account is not yet configured in Render environment."
    try:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        values = [[today_str, task, category, status, str(hours_spent), notes]]
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A:F",
            valueInputOption="USER_ENTERED",
            body={"values": values}
        ).execute()
        return f"Logged to tracker sheet: '{task}' ({hours_spent} hrs, {status})."
    except Exception as e:
        return f"Error logging to sheet: {str(e)}"

def get_progress_report() -> str:
    """Reads the Google Sheet and produces a summary report of past study sessions."""
    _, sheets_service = get_google_services()
    if not sheets_service or not SPREADSHEET_ID:
        return "Google Sheets is not yet configured."
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A2:F100"
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return "No entries recorded in tracker yet."
        summary = "Tracker Data:\n"
        for r in rows[-10:]:
            summary += f"- {r[0]} | {r[1]} ({r[2]}): {r[3]}, {r[4]} hrs. Notes: {r[5] if len(r)>5 else ''}\n"
        return summary
    except Exception as e:
        return f"Error reading sheet: {str(e)}"

AVAILABLE_TOOLS = [add_calendar_event, log_progress_to_sheet, get_progress_report]

@app.get("/")
def health():
    return {"status": "running"}

@app.post("/whatsapp")
async def whatsapp_webhook(Body: str = Form(""), From: str = Form("")):
    user_text = Body.strip()
    twiml = MessagingResponse()

    if not user_text:
        twiml.message("Please send a message.")
        return Response(content=str(twiml), media_type="application/xml")

    now_str = datetime.now(IST).strftime("%A, %Y-%m-%d %I:%M %p")
    system_prompt = f"""
You are a study and research personal assistant.
Current Date & Time in India: {now_str}.
Timezone: Asia/Kolkata (IST).

1. If asked conceptual questions on CA Final (FR, AFM, Audit, Tax) or political research, answer concisely and clearly.
2. If asked to schedule a study session or task, call `add_calendar_event` with ISO datetimes.
3. If reporting study progress or completed sums, call `log_progress_to_sheet`.
4. If asked for a report, call `get_progress_report` and summarize hours and subjects.
"""
    try:
        response = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
                tools=AVAILABLE_TOOLS
            )
        )
        answer = response.text.strip() if response.text else "Action completed."
    except Exception as e:
        answer = f"Error: {str(e)}"

    if len(answer) > 1500:
        answer = answer[:1450] + "\n\n...[Truncated]"

    twiml.message(answer)
    return Response(content=str(twiml), media_type="application/xml")
