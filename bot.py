import os
import json
import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ================= 1. 환경 변수 설정 (서버용) =================
# 서버(Render, GitHub Actions 등)에 올릴 때 이 값들을 환경변수로 등록합니다.
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
CALENDAR_ID = os.environ.get("CALENDAR_ID", "primary") # 없으면 primary
GOOGLE_JSON_STR = os.environ.get("GOOGLE_JSON") # JSON 파일 내용을 통째로 여기에 넣습니다.

app = App(token=SLACK_BOT_TOKEN)

# ================= 2. 역할 데이터 =================
ROLES_DB = {
    "role_trash": {
        "title": "🗑️ 2, 3층 쓰레기 (일반, 재활용)",
        "guide": "• [일반] 봉투째 배출 후 새 봉투 교체\n• [재활용] 종류별 배출 후 비닐 교체\n• [장소] 건물 앞 나무 옆"
    },
    "role_kitchen": {
        "title": "🍱 2층 주방 (냉장고, 음식물, 테이블)",
        "guide": "• [냉장고] 유통기한 음식 정리\n• [음식물] 거름통 비우기/세척 + 새 거름망\n• [정리] 싱크대, 테이블 닦기"
    },
    "role_machine": {
        "title": "☕ 2층 머신 (커피, 정수기)",
        "guide": "• [머신] 물받이/찌꺼기 통 세척\n• [원두] 부족 시 채우기\n• [정수기] 닦기"
    }
}

# 신청 현황: { "role_key:YYYY-MM-DD": "User_ID" }
assignments = {}

# ================= 3. 구글 캘린더 연동 (보안 강화 버전) =================
def create_google_calendar_event(user_email, role_title, description, target_date):
    if not GOOGLE_JSON_STR:
        print("❌ 구글 인증 키(GOOGLE_JSON)가 없습니다.")
        return None
        
    try:
        # JSON 문자열을 파이썬 딕셔너리로 변환 -> 바로 인증에 사용
        google_info = json.loads(GOOGLE_JSON_STR)
        creds = service_account.Credentials.from_service_account_info(
            google_info, scopes=['https://www.googleapis.com/auth/calendar']
        )
        service = build('calendar', 'v3', credentials=creds)

        start_time = f"{target_date}T10:00:00"
        end_time = f"{target_date}T11:00:00"

        event_body = {
            'summary': f'[클린매니저] {role_title}',
            'description': f"[자동 등록]\n{description}",
            'start': {'dateTime': start_time, 'timeZone': 'Asia/Seoul'},
            'end': {'dateTime': end_time, 'timeZone': 'Asia/Seoul'},
            'attendees': [{'email': user_email}],
        }
        event = service.events().insert(calendarId=CALENDAR_ID, body=event_body).execute()
        return event.get('htmlLink')
        
    except Exception as e:
        print(f"⚠️ 캘린더 등록 실패: {e}")
        return None

# ================= 4. UI 구성 =================
def build_main_blocks():
    blocks = [
        {
            "type": "section", 
            "text": {"type": "mrkdwn", "text": "🧹 *[클린매니저] 청소 담당자 모집*\n매주 *화요일 / 목요일*에 진행됩니다.\n원하는 날짜를 선택해주세요!"}
        },
        {"type": "divider"}
    ]

    for role_key, info in ROLES_DB.items():
        # 날짜별 신청자 리스트업
        taken_list = []
        for key, uid in assignments.items():
            if key.startswith(role_key + ":"):
                date_part = key.split(":")[1] # 2023-11-28
                short_date = date_part[5:].replace("-", "/") # 11/28
                taken_list.append(f"{short_date}(<@{uid}>)")
        
        taken_list.sort()
        status_text = f"👉 *{info['title']}*\n"
        if taken_list:
            status_text += f"✅ 확정: {', '.join(taken_list)}"
        else:
            status_text += "✨ 신청자가 없습니다."

        btn = {
            "type": "button", 
            "text": {"type": "plain_text", "text": "신청하기"}, 
            "action_id": "open_date_modal", 
            "value": role_key
        }
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": status_text}, "accessory": btn})
    
    return blocks

# ================= 5. 봇 로직 =================
@app.command("/클린")
def start(ack, say):
    ack()
    assignments.clear()
    say(blocks=build_main_blocks(), text="클린매니저 모집 시작")

@app.action("open_date_modal")
def open_modal(ack, body, client):
    ack()
    role_key = body["actions"][0]["value"]
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "submit_role_date",
            "private_metadata": f"{body['message']['ts']}|{body['channel']['id']}|{role_key}",
            "title": {"type": "plain_text", "text": "날짜 선택 (화/목)"},
            "submit": {"type": "plain_text", "text": "확정"},
            "close": {"type": "plain_text", "text": "취소"},
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"선택 역할: *{ROLES_DB[role_key]['title']}*\n📅 *화요일* 또는 *목요일*만 선택 가능합니다."}},
                {"type": "input", "block_id": "dp", "element": {"type": "datepicker", "action_id": "ds"}, "label": {"type": "plain_text", "text": "날짜 선택"}}
            ]
        }
    )

@app.view("submit_role_date")
def handle_sub(ack, body, client, view):
    date_str = view["state"]["values"]["dp"]["ds"]["selected_date"]
    sel_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    
    # [조건 1] 화(1) / 목(3) 체크
    if sel_date.weekday() not in [1, 3]:
        ack(response_action="errors", errors={"dp": "🚫 청소는 화요일과 목요일에만 가능합니다."})
        return

    ts, ch, role_key = view["private_metadata"].split("|")
    assign_key = f"{role_key}:{date_str}"

    # [조건 2] 해당 날짜+역할 중복 체크
    if assign_key in assignments:
        ack(response_action="errors", errors={"dp": f"😭 {date_str}에는 이미 담당자가 있습니다."})
        return

    ack()
    
    user_id = body["user"]["id"]
    assignments[assign_key] = user_id
    
    # 메시지 업데이트
    client.chat_update(channel=ch, ts=ts, blocks=build_main_blocks(), text="모집 현황 업데이트")
    
    # 캘린더 등록
    user_info = client.users_info(user=user_id)
    user_email = user_info["user"]["profile"].get("email")
    role = ROLES_DB[role_key]
    
    link = None
    if user_email:
        link = create_google_calendar_event(user_email, role['title'], role['guide'], date_str)
        
    msg = f"🎉 *{role['title']}* 확정!\n🗓️ {date_str} (화/목)\n📝 {role['guide']}"
    if link: msg += f"\n✅ <{link}|캘린더 일정 등록됨>"
    client.chat_postMessage(channel=user_id, text=msg)

if __name__ == "__main__":
    SocketModeHandler(app, SLACK_APP_TOKEN).start()