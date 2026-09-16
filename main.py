import os
import json
from datetime import datetime
import pytz
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
from google import genai
from google.genai import types
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI()

# 1. Environment & API Setup
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
CALENDAR_ID = os.environ.get("CALENDAR_ID", "primary")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

ai_client = genai.Client(api_key=GEMINI_KEY)

# Initialize Google Workspace Services
def get_google_services():
    if not SERVICE_ACCOUNT_JSON:
        return None, None
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/spreadsheets"
        ]
    )
    calendar_service = build("calendar", "v3", credentials=creds)
    sheets_service = build("sheets", "v4", credentials=creds)
    return calendar_service, sheets_service

# 2. Tool 1: Add Event to Google Calendar
def add_calendar_event(summary: str, start_datetime: str, end_datetime: str, description: str = "") -> str:
    """Adds a study session, task, or research event to Google Calendar.
    Args:
        summary: Title of the event (e.g., 'CA Final AFM Forex Practice').
        start_datetime: Event start in ISO format 'YYYY-MM-DDTHH:MM:SS' (e.g., '2026-09-17T16:00:00').
        end_datetime: Event end in ISO format 'YYYY-MM-DDTHH:MM:SS' (e.g., '2026-09-17T18:00:00').
        description: Details or notes about the study session.
    """
    cal_service, _ = get_google_services()
    if not cal_service:
        return "Google Calendar is not configured."
    try:
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": f"{start_datetime}+05:30", "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": f"{end_datetime}+05:30", "timeZone": "Asia/Kolkata"},
        }
        created = cal_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return f"Event successfully created in Google Calendar: '{summary}' on {start_datetime}."
    except Exception as e:
        return f"Failed to create calendar event: {str(e)}"

# 3. Tool 2: Log Task or Session to Progress Sheet
def log_progress_to_sheet(task: str, category: str, status: str, hours_spent: float, notes: str = "") -> str:
    """Logs a completed or in-progress study/research task into the Google Sheet progress tracker.
    Args:
        task: Name of the task or chapter (e.g., 'Ind AS 115 Revenue Recognition').
        category: 'CA Final' or 'Policy Research' or 'General'.
        status: 'Completed', 'In Progress', or 'Pending'.
        hours_spent: Number of hours spent (e.g., 2.5).
        notes: Any takeaways or observations.
    """
    _, sheets_service = get_google_services()
    if not sheets_service or not SPREADSHEET_ID:
        return "Google Sheets is not configured."
    try:
        today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
        values = [[today_str, task, category, status, str(hours_spent), notes]]
        body = {"values": values}
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A:F",
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        return f"Task logged in tracker sheet: '{task}' ({hours_spent} hrs, {status})."
    except Exception as e:
        return f"Failed to log task to Sheet: {str(e)}"

# 4. Tool 3: Generate Summary Report from Sheet
def get_progress_report(days_back: int = 7) -> str:
    """Reads the Google Sheet and produces a summary progress report of study and research activities.
    Args:
        days_back: Number of days to include in the progress report (default 7 for weekly report).
    """
    _, sheets_service = get_google_services()
    if not sheets_service or not SPREADSHEET_ID:
        return "Google Sheets is not configured."
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A2:F100"
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return "No entries logged in the sheet yet."
        summary = "Tracker Data:\n"
        for r in rows[-15:]:  # show recent 15 entries
            summary += f"- {r[0]} | {r[1]} ({r[2]}): {r[3]}, {r[4]} hrs. Notes: {r[5] if len(r) > 5 else ''}\n"
        return summary
    except Exception as e:
        return f"Failed to read sheet data: {str(e)}"

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

    # Current IST timestamp so the bot understands relative terms like 'today', 'tomorrow 4 PM'
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%A, %Y-%m-%d %I:%M %p")
    system_prompt = f"""
You are a personal assistant for a CA Final student and political researcher.
Current Date & Time in India: {now_ist}.
Timezone: Asia/Kolkata (IST).

Capabilities:
1. Answer conceptual CA Final questions (FR, AFM, Auditing, Tax) and policy/research questions.
2. If asked to schedule, set a task, or plan a study session, call `add_calendar_event`. Calculate the exact start and end ISO datetimes based on the current date/time above.
3. If the user reports finishing a study session, practicing sums, or reading a paper, call `log_progress_to_sheet`.
4. If asked for a daily or weekly progress report, call `get_progress_report` and summarize their total study hours, topics covered, and remaining tasks clearly.

Keep final WhatsApp responses crisp, structured, and easy to read on mobile.
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
    return Response(content=str(twiml), media_type="application/xml")
