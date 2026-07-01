import requests
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import time
import re
import os


# =========================
# CONFIG
# =========================

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]

GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "Beat-Sales-CRM")
SERVICE_ACCOUNT_FILE = "service_account.json"

DEFAULT_SEARCH_TERMS = [
    "Brent Faiyaz type beat",
    "PARTYNEXTDOOR type beat",
    "dark r&b type beat",
    "Bryson Tiller type beat",
    "Summer Walker type beat",
    "Drake r&b type beat"
]

DEFAULT_BUYER_KEYWORDS = [
    "price",
    "lease",
    "buy",
    "cost"
]

HEADERS = [
    "run_id",
    "date_found",
    "artist_user",
    "matched_keyword",
    "comment",
    "video_title",
    "video_link",
    "lead_status",
    "platform_to_contact",
    "profile_link",
    "message_sent",
    "follow_up_date",
    "sale_status",
    "sale_amount",
    "message_draft"
]


# =========================
# GOOGLE SHEETS SETUP
# =========================

def connect_to_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=scopes
    )

    client = gspread.authorize(credentials)
    return client.open(GOOGLE_SHEET_NAME)


def get_or_create_worksheet(spreadsheet, title, rows=1000, cols=30):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def setup_base_tabs(spreadsheet):
    dashboard = get_or_create_worksheet(
        spreadsheet,
        "Dashboard",
        rows=60,
        cols=10
    )

    settings = get_or_create_worksheet(
        spreadsheet,
        "Settings",
        rows=50,
        cols=10
    )

    master = get_or_create_worksheet(
        spreadsheet,
        "Master Leads",
        rows=5000,
        cols=len(HEADERS)
    )

    if not settings.get_all_values():
        settings.update(
            range_name="A1:B7",
            values=[
                ["Setting", "Value"],
                ["search_terms", ", ".join(DEFAULT_SEARCH_TERMS)],
                ["buyer_keywords", ", ".join(DEFAULT_BUYER_KEYWORDS)],
                ["max_leads_per_run", "100"],
                ["videos_per_search", "10"],
                ["comments_per_video", "100"],
                ["min_keyword_match", "1"]
            ]
        )

    # Always reset the Master Leads header row
    master.update(
        range_name="A1:O1",
        values=[HEADERS]
    )

    apply_basic_filter(master)

    return dashboard, settings, master


def read_settings(settings_ws):
    values = settings_ws.get_all_records()
    settings = {}

    for row in values:
        key = str(row.get("Setting", "")).strip()
        value = str(row.get("Value", "")).strip()

        if key:
            settings[key] = value

    search_terms = [
        item.strip()
        for item in settings.get(
            "search_terms",
            ", ".join(DEFAULT_SEARCH_TERMS)
        ).split(",")
        if item.strip()
    ]

    buyer_keywords = [
        item.strip().lower()
        for item in settings.get(
            "buyer_keywords",
            ", ".join(DEFAULT_BUYER_KEYWORDS)
        ).split(",")
        if item.strip()
    ]

    return {
        "search_terms": search_terms,
        "buyer_keywords": buyer_keywords,
        "max_leads_per_run": int(settings.get("max_leads_per_run", 100)),
        "videos_per_search": int(settings.get("videos_per_search", 10)),
        "comments_per_video": int(settings.get("comments_per_video", 100)),
        "min_keyword_match": int(settings.get("min_keyword_match", 1))
    }


def get_next_run_name(spreadsheet):
    existing_titles = [ws.title for ws in spreadsheet.worksheets()]
    run_numbers = []

    for title in existing_titles:
        if title.startswith("Pull "):
            try:
                number = int(title.replace("Pull ", "").strip())
                run_numbers.append(number)
            except ValueError:
                pass

    next_number = max(run_numbers) + 1 if run_numbers else 1
    return f"Pull {next_number:03d}"


def apply_basic_filter(worksheet):
    try:
        worksheet.set_basic_filter()
    except Exception as error:
        print(f"Could not apply filter to {worksheet.title}: {error}")


# =========================
# YOUTUBE API
# =========================

def youtube_search(query, max_results=10):
    url = "https://www.googleapis.com/youtube/v3/search"

    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "key": YOUTUBE_API_KEY
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        print("YouTube search error:")
        print(response.status_code)
        print(response.text)
        return []

    return response.json().get("items", [])


def get_comments(video_id, max_results=100):
    url = "https://www.googleapis.com/youtube/v3/commentThreads"

    params = {
        "part": "snippet",
        "videoId": video_id,
        "maxResults": max_results,
        "textFormat": "plainText",
        "key": YOUTUBE_API_KEY
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        return []

    return response.json().get("items", [])


# =========================
# LEAD FILTERING
# =========================

def clean_text(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def find_matched_keywords(comment, buyer_keywords):
    text = comment.lower()
    matches = []

    for keyword in buyer_keywords:
        if keyword in text:
            matches.append(keyword)

    return matches


def create_message(author, comment, video_title):
    return (
        f"Yo, I saw your comment asking about the beat on '{video_title}'. "
        f"I produce smooth/dark R&B beats too, and I may have one that fits your sound. "
        f"Are you working on something right now?"
    )


def make_lead_row(run_id, author, matched_keywords, comment_text, video_title, video_link):
    date_found = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return [
        run_id,
        date_found,
        clean_text(author),
        ", ".join(matched_keywords),
        clean_text(comment_text),
        clean_text(video_title),
        video_link,
        "Not Contacted",
        "",
        "",
        "No",
        "",
        "Open",
        "",
        create_message(author, comment_text, video_title)
    ]


def get_existing_unique_keys(master_ws):
    rows = master_ws.get_all_values()
    keys = set()

    # Skip header row
    for row in rows[1:]:
        if len(row) < 7:
            continue

        artist = str(row[2]).lower().strip()
        comment = str(row[4]).lower().strip()
        video_link = str(row[6]).lower().strip()

        if artist and comment and video_link:
            keys.add(f"{artist}|{comment}|{video_link}")

    return keys


def lead_unique_key(row):
    artist = str(row[2]).lower().strip()
    comment = str(row[4]).lower().strip()
    video_link = str(row[6]).lower().strip()
    return f"{artist}|{comment}|{video_link}"


# =========================
# GOOGLE SHEETS STYLING HELPERS
# =========================

def hex_to_rgb(hex_color):
    hex_color = hex_color.replace("#", "")

    return {
        "red": int(hex_color[0:2], 16) / 255,
        "green": int(hex_color[2:4], 16) / 255,
        "blue": int(hex_color[4:6], 16) / 255
    }


def format_range(
    sheet_id,
    start_row,
    end_row,
    start_col,
    end_col,
    bg=None,
    fg=None,
    bold=False,
    font_size=10,
    horizontal="LEFT",
    vertical="MIDDLE"
):
    cell_format = {
        "textFormat": {
            "bold": bold,
            "fontSize": font_size
        },
        "horizontalAlignment": horizontal,
        "verticalAlignment": vertical
    }

    fields = [
        "userEnteredFormat.textFormat",
        "userEnteredFormat.horizontalAlignment",
        "userEnteredFormat.verticalAlignment"
    ]

    if bg:
        cell_format["backgroundColor"] = hex_to_rgb(bg)
        fields.append("userEnteredFormat.backgroundColor")

    if fg:
        cell_format["textFormat"]["foregroundColor"] = hex_to_rgb(fg)
        fields.append("userEnteredFormat.textFormat.foregroundColor")

    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": start_col,
                "endColumnIndex": end_col
            },
            "cell": {
                "userEnteredFormat": cell_format
            },
            "fields": ",".join(fields)
        }
    }


def set_borders(sheet_id, start_row, end_row, start_col, end_col, color="#DADCE0"):
    border = {
        "style": "SOLID",
        "width": 1,
        "color": hex_to_rgb(color)
    }

    return {
        "updateBorders": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": start_col,
                "endColumnIndex": end_col
            },
            "top": border,
            "bottom": border,
            "left": border,
            "right": border,
            "innerHorizontal": border,
            "innerVertical": border
        }
    }


def merge_cells(sheet_id, start_row, end_row, start_col, end_col):
    return {
        "mergeCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": start_col,
                "endColumnIndex": end_col
            },
            "mergeType": "MERGE_ALL"
        }
    }


def set_column_width(sheet_id, start_col, end_col, width):
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": start_col,
                "endIndex": end_col
            },
            "properties": {
                "pixelSize": width
            },
            "fields": "pixelSize"
        }
    }


def set_row_height(sheet_id, start_row, end_row, height):
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": start_row,
                "endIndex": end_row
            },
            "properties": {
                "pixelSize": height
            },
            "fields": "pixelSize"
        }
    }


# =========================
# DASHBOARD
# =========================

def update_dashboard(dashboard_ws, master_ws, run_id, leads_added):
    spreadsheet = dashboard_ws.spreadsheet
    dashboard_ws.clear()

    dashboard_data = [
        ["Beat Sales CRM", "", "", "", "", "", "", ""],
        ["YouTube buyer-intent lead tracker for $50 beat leases", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],

        ["Total Leads", "Not Contacted", "Potential Client", "Messaged", "Replied", "Beat Sent", "Closed Won", "Revenue"],
        [
            '=COUNTA(\'Master Leads\'!C2:C)',
            '=COUNTIF(\'Master Leads\'!H:H,"Not Contacted")',
            '=COUNTIF(\'Master Leads\'!H:H,"Potential Client")',
            '=COUNTIF(\'Master Leads\'!H:H,"Messaged")',
            '=COUNTIF(\'Master Leads\'!H:H,"Replied")',
            '=COUNTIF(\'Master Leads\'!H:H,"Beat Sent")',
            '=COUNTIF(\'Master Leads\'!M:M,"Closed Won")',
            '=SUM(\'Master Leads\'!N:N)'
        ],
        ["", "", "", "", "", "", "", ""],

        ["Pipeline Overview", "", "", "", "", "", "", ""],
        ["Stage", "Count", "Action Needed", "", "Run Summary", "Value", "", ""],
        ["Not Contacted", '=COUNTIF(\'Master Leads\'!H:H,"Not Contacted")', "Review lead and confirm they are an artist", "", "Last Run", run_id, "", ""],
        ["Potential Client", '=COUNTIF(\'Master Leads\'!H:H,"Potential Client")', "Find profile link and prepare/send first DM", "", "Leads Added", leads_added, "", ""],
        ["Messaged", '=COUNTIF(\'Master Leads\'!H:H,"Messaged")', "Wait for reply or follow up", "", "Target Leads Per Run", '=Settings!B4', "", ""],
        ["Replied", '=COUNTIF(\'Master Leads\'!H:H,"Replied")', "Qualify and send best beat", "", "Buyer Keywords", '=Settings!B3', "", ""],
        ["Beat Sent", '=COUNTIF(\'Master Leads\'!H:H,"Beat Sent")', "Follow up within 24 hours", "", "Search Terms", '=Settings!B2', "", ""],
        ["Closed Won", '=COUNTIF(\'Master Leads\'!M:M,"Closed Won")', "Collect song/repost/testimonial", "", "", "", "", ""],
        ["Closed Lost", '=COUNTIF(\'Master Leads\'!M:M,"Closed Lost")', "Move on or revisit later", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],

        ["Sales Performance", "", "", "", "", "", "", ""],
        ["Metric", "Value", "Goal", "", "Quick Next Steps", "", "", ""],
        ["Review Rate", '=IFERROR(COUNTIF(\'Master Leads\'!H:H,"Potential Client")/COUNTA(\'Master Leads\'!C2:C),0)', "50%+", "", "1. Review Not Contacted leads first", "", "", ""],
        ["Reply Rate", '=IFERROR(COUNTIF(\'Master Leads\'!H:H,"Replied")/COUNTIF(\'Master Leads\'!H:H,"Messaged"),0)', "10%+", "", "2. Move good artists to Potential Client", "", "", ""],
        ["Close Rate", '=IFERROR(COUNTIF(\'Master Leads\'!M:M,"Closed Won")/COUNTA(\'Master Leads\'!C2:C),0)', "2%+", "", "3. Message 25 Potential Clients today", "", "", ""],
        ["Average Sale", '=IFERROR(SUM(\'Master Leads\'!N:N)/COUNTIF(\'Master Leads\'!M:M,"Closed Won"),0)', "$50", "", "4. Follow up after 24 hours", "", "", ""],
        ["Sales Needed for $500", '=IFERROR(ROUNDUP((500-SUM(\'Master Leads\'!N:N))/50,0),10)', "10 sales", "", "5. Send one best-fit beat after they reply", "", "", ""],
    ]

    dashboard_ws.update(
    range_name="A1:H23",
    values=dashboard_data,
    value_input_option="USER_ENTERED"
)

    sheet_id = dashboard_ws.id
    requests = []

    requests.append(merge_cells(sheet_id, 0, 1, 0, 8))
    requests.append(merge_cells(sheet_id, 1, 2, 0, 8))
    requests.append(merge_cells(sheet_id, 6, 7, 0, 8))
    requests.append(merge_cells(sheet_id, 16, 17, 0, 8))

    requests.append(format_range(
        sheet_id, 0, 1, 0, 8,
        bg="#111827",
        fg="#FFFFFF",
        bold=True,
        font_size=18,
        horizontal="CENTER"
    ))

    requests.append(format_range(
        sheet_id, 1, 2, 0, 8,
        bg="#1F2937",
        fg="#D1D5DB",
        font_size=10,
        horizontal="CENTER"
    ))

    requests.append(format_range(
        sheet_id, 3, 4, 0, 8,
        bg="#E5E7EB",
        fg="#111827",
        bold=True,
        font_size=10,
        horizontal="CENTER"
    ))

    requests.append(format_range(
        sheet_id, 4, 5, 0, 8,
        bg="#F9FAFB",
        fg="#111827",
        bold=True,
        font_size=14,
        horizontal="CENTER"
    ))

    requests.append(set_borders(sheet_id, 3, 5, 0, 8, color="#D1D5DB"))

    requests.append(format_range(
        sheet_id, 6, 7, 0, 8,
        bg="#111827",
        fg="#FFFFFF",
        bold=True,
        font_size=12
    ))

    requests.append(format_range(
        sheet_id, 16, 17, 0, 8,
        bg="#111827",
        fg="#FFFFFF",
        bold=True,
        font_size=12
    ))

    requests.append(format_range(
        sheet_id, 7, 8, 0, 3,
        bg="#374151",
        fg="#FFFFFF",
        bold=True,
        font_size=10
    ))

    requests.append(format_range(
        sheet_id, 7, 8, 4, 6,
        bg="#374151",
        fg="#FFFFFF",
        bold=True,
        font_size=10
    ))

    requests.append(format_range(
        sheet_id, 17, 18, 0, 3,
        bg="#374151",
        fg="#FFFFFF",
        bold=True,
        font_size=10
    ))

    requests.append(format_range(
        sheet_id, 17, 18, 4, 8,
        bg="#374151",
        fg="#FFFFFF",
        bold=True,
        font_size=10
    ))

    requests.append(format_range(
        sheet_id, 8, 15, 0, 3,
        bg="#F9FAFB",
        fg="#111827",
        font_size=10
    ))

    requests.append(format_range(
        sheet_id, 8, 15, 4, 6,
        bg="#F9FAFB",
        fg="#111827",
        font_size=10
    ))

    requests.append(format_range(
        sheet_id, 18, 22, 0, 3,
        bg="#F9FAFB",
        fg="#111827",
        font_size=10
    ))

    requests.append(format_range(
        sheet_id, 18, 22, 4, 8,
        bg="#F9FAFB",
        fg="#111827",
        font_size=10
    ))

    requests.append(set_borders(sheet_id, 7, 15, 0, 3))
    requests.append(set_borders(sheet_id, 7, 15, 4, 6))
    requests.append(set_borders(sheet_id, 17, 22, 0, 3))
    requests.append(set_borders(sheet_id, 17, 22, 4, 8))

    requests.append(set_column_width(sheet_id, 0, 1, 145))
    requests.append(set_column_width(sheet_id, 1, 2, 130))
    requests.append(set_column_width(sheet_id, 2, 3, 260))
    requests.append(set_column_width(sheet_id, 3, 4, 35))
    requests.append(set_column_width(sheet_id, 4, 5, 160))
    requests.append(set_column_width(sheet_id, 5, 6, 260))
    requests.append(set_column_width(sheet_id, 6, 8, 120))

    requests.append(set_row_height(sheet_id, 0, 1, 38))
    requests.append(set_row_height(sheet_id, 1, 2, 28))
    requests.append(set_row_height(sheet_id, 3, 5, 42))
    requests.append(set_row_height(sheet_id, 6, 7, 32))
    requests.append(set_row_height(sheet_id, 16, 17, 32))

    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": 3
                }
            },
            "fields": "gridProperties.frozenRowCount"
        }
    })

    spreadsheet.batch_update({"requests": requests})

    dashboard_ws.format("H5", {
        "numberFormat": {
            "type": "CURRENCY",
            "pattern": "$#,##0"
        }
    })

    dashboard_ws.format("B18:B20", {
        "numberFormat": {
            "type": "PERCENT",
            "pattern": "0.0%"
        }
    })

    dashboard_ws.format("B21", {
        "numberFormat": {
            "type": "CURRENCY",
            "pattern": "$#,##0"
        }
    })

    apply_basic_filter(dashboard_ws)


# =========================
# MASTER LEADS STYLING
# =========================

def style_master_leads(master_ws):
    spreadsheet = master_ws.spreadsheet
    sheet_id = master_ws.id

    requests = []

    requests.append(format_range(
        sheet_id,
        0,
        1,
        0,
        len(HEADERS),
        bg="#111827",
        fg="#FFFFFF",
        bold=True,
        font_size=10,
        horizontal="CENTER"
    ))

    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": 1
                }
            },
            "fields": "gridProperties.frozenRowCount"
        }
    })

    widths = {
        0: 90,
        1: 145,
        2: 150,
        3: 130,
        4: 330,
        5: 300,
        6: 260,
        7: 130,
        8: 140,
        9: 220,
        10: 115,
        11: 130,
        12: 115,
        13: 105,
        14: 360
    }

    for col_index, width in widths.items():
        requests.append(set_column_width(
            sheet_id,
            col_index,
            col_index + 1,
            width
        ))

    spreadsheet.batch_update({"requests": requests})

    master_ws.format("A:O", {
        "wrapStrategy": "WRAP",
        "verticalAlignment": "MIDDLE"
    })

    master_ws.format("N:N", {
        "numberFormat": {
            "type": "CURRENCY",
            "pattern": "$#,##0"
        }
    })

    apply_basic_filter(master_ws)


def style_run_tab(run_ws):
    spreadsheet = run_ws.spreadsheet
    sheet_id = run_ws.id

    requests = []

    requests.append(format_range(
        sheet_id,
        0,
        1,
        0,
        len(HEADERS),
        bg="#111827",
        fg="#FFFFFF",
        bold=True,
        font_size=10,
        horizontal="CENTER"
    ))

    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": 1
                }
            },
            "fields": "gridProperties.frozenRowCount"
        }
    })

    widths = {
        0: 90,
        1: 145,
        2: 150,
        3: 130,
        4: 330,
        5: 300,
        6: 260,
        7: 130,
        8: 140,
        9: 220,
        10: 115,
        11: 130,
        12: 115,
        13: 105,
        14: 360
    }

    for col_index, width in widths.items():
        requests.append(set_column_width(
            sheet_id,
            col_index,
            col_index + 1,
            width
        ))

    spreadsheet.batch_update({"requests": requests})

    run_ws.format("A:O", {
        "wrapStrategy": "WRAP",
        "verticalAlignment": "MIDDLE"
    })

    run_ws.format("N:N", {
        "numberFormat": {
            "type": "CURRENCY",
            "pattern": "$#,##0"
        }
    })

    apply_basic_filter(run_ws)


# =========================
# MAIN SCRAPER
# =========================

def run_scraper():
    spreadsheet = connect_to_sheet()
    dashboard_ws, settings_ws, master_ws = setup_base_tabs(spreadsheet)

    settings = read_settings(settings_ws)

    run_id = get_next_run_name(spreadsheet)

    run_ws = spreadsheet.add_worksheet(
        title=run_id,
        rows=settings["max_leads_per_run"] + 10,
        cols=len(HEADERS)
    )

    run_ws.update(
    range_name="A1:O1",
    values=[HEADERS]
)

    existing_keys = get_existing_unique_keys(master_ws)
    run_leads = []

    print(f"Starting {run_id}")
    print(f"Search terms: {settings['search_terms']}")
    print(f"Buyer keywords: {settings['buyer_keywords']}")

    for term in settings["search_terms"]:
        if len(run_leads) >= settings["max_leads_per_run"]:
            break

        print(f"Searching videos for: {term}")
        videos = youtube_search(term, settings["videos_per_search"])

        for video in videos:
            if len(run_leads) >= settings["max_leads_per_run"]:
                break

            video_id = video["id"]["videoId"]
            video_title = video["snippet"]["title"]
            video_link = f"https://www.youtube.com/watch?v={video_id}"

            print(f"Checking comments: {video_title}")
            comments = get_comments(
                video_id,
                settings["comments_per_video"]
            )

            for item in comments:
                if len(run_leads) >= settings["max_leads_per_run"]:
                    break

                snippet = item["snippet"]["topLevelComment"]["snippet"]
                author = snippet.get("authorDisplayName", "")
                comment_text = snippet.get("textDisplay", "")

                matched_keywords = find_matched_keywords(
                    comment_text,
                    settings["buyer_keywords"]
                )

                if len(matched_keywords) < settings["min_keyword_match"]:
                    continue

                lead_row = make_lead_row(
                    run_id,
                    author,
                    matched_keywords,
                    comment_text,
                    video_title,
                    video_link
                )

                unique_key = lead_unique_key(lead_row)

                if unique_key in existing_keys:
                    continue

                existing_keys.add(unique_key)
                run_leads.append(lead_row)

            time.sleep(0.2)

    if run_leads:
        run_ws.update(
            range_name=f"A2:O{len(run_leads) + 1}",
            values=run_leads,
            value_input_option="USER_ENTERED"
        )   

        master_ws.append_rows(
            run_leads,
            value_input_option="USER_ENTERED"
        )

    style_run_tab(run_ws)
    style_master_leads(master_ws)
    update_dashboard(dashboard_ws, master_ws, run_id, len(run_leads))

    print(f"Done. Added {len(run_leads)} leads.")
    print(f"New tab created: {run_id}")


if __name__ == "__main__":
    run_scraper()
