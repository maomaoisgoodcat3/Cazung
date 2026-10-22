# Cazung ♥ — AI agent cá nhân sống trên desktop

Cazung là một cô gái chibi pixel 18 tuổi đứng trên taskbar của bạn. Cô ấy đi dạo, ngủ gật, phản ứng theo cảm xúc,
và khi bạn bấm vào thì mở khung chat để nhận lệnh. Phía sau là một agent chạy **model local không kiểm duyệt
(Dolphin qua Ollama)**, có **tool calling**, **trí nhớ ngắn hạn/dài hạn chống quên**, và **tự học từ phản hồi**.
Mọi thứ chạy trên máy bạn, không gửi dữ liệu đi đâu cả (trừ khi Cazung dùng tool web).

## Cài đặt & chạy

1. Cài [Ollama](https://ollama.com/download) (đã có trên máy bạn), Python 3.11+ và Node.js 18+.
2. Chạy `setup.bat` một lần. Script tạo venv, cài Electron vào trong thư mục này và tải 2 model:
   - `dolphin3`: model chat chính (~4.9 GB, bản Llama 3.1 8B không kiểm duyệt)
   - `nomic-embed-text`: model embedding cho trí nhớ (~270 MB)
3. Chạy `start.bat`. Cazung xuất hiện ở góc phải dưới màn hình, và backend được tự khởi động.

Muốn đổi model thì sửa `config.toml` → `[llm] model = "..."` (ví dụ `dolphin-mistral`, `dolphin-mixtral`,
`dolphin-llama3:70b`…). Dùng được bất kỳ model nào của Ollama, hoặc bất kỳ server nào tương thích OpenAI
(LM Studio, llama.cpp, KoboldCpp…) bằng cách đặt `provider = "openai"`.

> Dolphin nói tiếng Việt ở mức khá. Nếu muốn Cazung nói tiếng Việt tự nhiên hơn, hãy thử các bản Qwen
> "abliterated/uncensored" trên Ollama, và đặt `utility_model` là một model nhỏ để các việc nền chạy nhanh hơn.

## Cách chơi với Cazung

| Thao tác | Kết quả |
|---|---|
| Bấm vào Cazung | Mở/đóng khung chat |
| Bấm đúp | Cazung nhảy / nhún nhảy |
| Kéo thả | Nhấc Cazung lên (cô ấy lắc lư, toát mồ hôi), thả ra thì rơi xuống taskbar |
| Rê chuột lại gần | Mắt nhìn theo chuột |
| Rê chuột qua lại trên đầu | Xoa đầu ♥ |
| Chuột phải | Menu: chat mới, xem bộ nhớ, ngủ, tắt đi dạo, mở workspace, sửa config, thoát |
| `Ctrl+Alt+C` | Mở chat từ bất kỳ đâu |
| 👍 / 👎 dưới câu trả lời | Cazung rút kinh nghiệm (👎 kèm góp ý sẽ kích hoạt tự suy ngẫm ngay) |

Khung chat cũng mở được trên trình duyệt tại `http://127.0.0.1:8765` (chạy `start-backend.bat` nếu chỉ muốn backend).

Lệnh nhanh trong chat: `/new` (cuộc trò chuyện mới), `/remember <câu>`, `/forget <id>`, `/memory`,
`/compact` (nén hội thoại ngay), `/reflect` (tự suy ngẫm ngay), `/maintain` (dọn bộ nhớ), `/help`.

## Kiến trúc

```
frontend/ (Electron)                         backend/ (Python, FastAPI)
┌──────────────────────────┐   WebSocket    ┌──────────────────────────────────────────┐
│ main.js    cửa sổ trong   │◄─────────────►│ agent.py    vòng lặp suy nghĩ → tool → trả lời│
│            suốt, kéo thả, │   + REST       │ llm.py      Ollama / OpenAI-compatible        │
│            khay, phím tắt │   (token)      │ persona.py  tính cách + giao thức gọi tool    │
│ renderer/  sprite pixel,  │                │ memory/     trí nhớ nhiều tầng (xem dưới)     │
│            hoạt ảnh, chat │                │ tools/      tool có sẵn + plugins/ tự nạp     │
└──────────────────────────┘                │ worker.py   việc nền (trích xuất, nén, tự học)│
                                            └──────────────────────────────────────────┘
                                                   data/cazung.db (SQLite, mọi thứ ở đây)
```

### Hoạt ảnh
Khi rảnh, Cazung tự đi dạo, ngồi xuống, nhìn quanh, vươn vai, ngân nga, thỉnh thoảng nhún nhảy, và ngáp rồi ngủ khi không ai chơi.
Khi làm việc, cô ấy có đám mây suy nghĩ lúc nghĩ, mở laptop lúc code/chạy lệnh, cầm kính lúp lúc tìm web,
quay bánh răng lúc rà soát máy, thả phong thư lúc xử lý email, và cử động môi theo chữ đang hiện.
Câu trả lời vui thì cô ấy nhảy lên, buồn thì rơi nước mắt.

### Chữ hiện mượt & chế độ suy nghĩ
Token từ model đi qua một bộ "gõ chữ" thích ứng: khi đã theo kịp thì chữ hiện khoảng 40 ký tự/giây,
còn khi model sinh nhanh hơn thì tăng tốc từ từ, nên không bị khựng hay dồn cục. Ngay khi bạn gửi tin là có
dấu "đang gõ…", và chip tool hiện đúng thứ tự sau đoạn chữ trước nó.
Với model có chế độ suy nghĩ (qwen3, deepseek-r1…), bật `think = true` trong `config.toml`: luồng suy nghĩ chạy trực tiếp
trong khung 💭 kèm đồng hồ đếm giây, và tự thu gọn khi câu trả lời bắt đầu. Model trả `<think>…</think>` trong nội dung
thì luôn được tách riêng như vậy.

### Tool calling với mọi model
Dolphin không có function calling native, nên Cazung dùng giao thức văn bản:
`<tool>{"name": "...", "args": {...}}</tool>`. Parser đọc stream token theo thời gian thực, ẩn khối tool khỏi
giao diện, chịu được JSON lỗi (dư/thiếu ngoặc, nháy đơn), nhận cả JSON trần, và báo lỗi để model tự sửa.
Một lượt có thể gọi tối đa `max_steps` tool liên tiếp.

### Chống quên trong chat dài: 6 tầng trí nhớ
Mỗi lượt, prompt được dựng lại trong ngân sách token (`num_ctx`) theo thứ tự:

1. **Trí nhớ cốt lõi**: luật bạn dặn, sự thật và sở thích của bạn (quan trọng nhất). Luôn có trong prompt.
2. **Ký ức liên quan**: tìm theo nghĩa (embedding) cộng điểm độ quan trọng, độ mới và từ khóa trùng.
3. **Bài học & kỹ năng** đã tự học được.
4. **Trích đoạn nguyên văn** từ hội thoại cũ, tìm bằng full-text (không phân biệt dấu: "da nang" khớp "Đà Nẵng").
5. **Bản tóm tắt cuộn** của cuộc trò chuyện hiện tại.
6. **Cửa sổ ngắn hạn**: các tin nhắn gần nhất, nguyên văn, vừa khít ngân sách còn lại.

Khi lịch sử vượt `compact_trigger` (75%) ngân sách, các tin cũ nhất được **gộp vào bản tóm tắt** và đồng thời
lưu thành một **ký ức "episode"** có embedding. Việc này chạy nền nên không làm chậm câu trả lời.
**Không tin nhắn nào bị xóa**: mọi thứ vẫn tìm lại được bằng tool `search_history`. Đã có test tự động chứng minh
điều này: 150 lượt chat nhồi vào ngữ cảnh 1.5k token, mà thông tin ở tin nhắn đầu tiên vẫn được nhớ lại nguyên văn.

Sau mỗi lượt, Cazung **tự trích xuất ký ức** mới về bạn (tên, nghề, sở thích, lịch hẹn…). Ký ức trùng sẽ được gộp,
ký ức cũ bị mâu thuẫn sẽ được cập nhật. Nếu chưa có model embedding, hệ thống tự dùng embedding hash
(so khớp từ khóa), và tự embed lại toàn bộ khi model thật sẵn sàng.

### Tự học & phát triển
- Cứ mỗi `reflect_every_turns` lượt, hoặc ngay khi bạn bấm 👎, Cazung xem lại các tương tác gần đây cùng phản hồi,
  rồi rút ra **bài học** (ví dụ "chủ nhân thích câu trả lời ngắn, có code mẫu"). Bài học được đưa vào prompt các lần sau.
- Khi làm xong một việc nhiều bước, Cazung lưu nó thành **kỹ năng** (quy trình) để dùng lại cho việc tương tự.
- Bảo trì định kỳ: gộp ký ức trùng, giảm độ quan trọng của ký ức lâu không dùng, embed lại khi đổi model.
- Bạn xem, thêm và xóa mọi ký ức ở nút 🧠 trong khung chat. Tab "Thống kê" cho thấy ngữ cảnh đang dùng và bản tóm tắt.

## Tool có sẵn
| Nhóm | Tool |
|---|---|
| Code & file | `tree`, `list_files`, `read_file` (theo khoảng dòng), `search_code` (regex), `code_outline`, `write_file`, `edit_file`, `delete_file`, `run_python`, `run_shell` |
| Máy tính (chỉ đọc) | `system_overview`, `list_processes`, `process_details`, `network_connections`, `startup_programs`, `installed_software`, `system_scan` |
| Email | `read_emails` (IMAP, không đánh dấu đã đọc), `send_email` (SMTP) |
| Web | `web_search`, `fetch_url`, `open_url`, `get_weather` |
| Trí nhớ | `remember`, `recall`, `forget`, `search_history`, `save_skill` |
| Khác | `calculator`, `get_time`, `set_reminder` / `list_reminders` / `cancel_reminder`, `clipboard_read` / `clipboard_write`, `open_app` |
| MCP | mọi tool của các server MCP bạn khai báo, tên dạng `<server>__<tool>` |

Đọc và khám phá mã nguồn được ở mọi thư mục (đường dẫn tuyệt đối); đường dẫn tương đối thì tính trong `data/workspace`.
Email cần điền mục `[email]` trong config (Gmail dùng App Password).

### Kết nối tool ngoài (MCP)
Thêm khối `[[mcp]]` vào `config.toml` (đã có ví dụ GitHub và filesystem). Khi khởi động, Cazung chạy server đó,
lấy danh sách tool và dùng được ngay. Có hàng trăm server MCP sẵn có: GitHub, GitLab, Slack, Notion, Google Drive,
trình duyệt (Playwright), database… Mặc định **mọi tool MCP đều phải xin phép**; `auto_approve` chỉ nên chứa tool chỉ-đọc.

### Tự viết tool mới
Tạo một file `.py` trong `backend/plugins/`. File sẽ được nạp tự động khi khởi động:

```python
from cazung.tools import tool

@tool("Mô tả cho model biết khi nào dùng", {"city": "str: tên thành phố", "?days": "int: tùy chọn"})
async def my_tool(city: str, days: int = 1) -> str:
    return "kết quả dạng chữ"
```
Thêm `dangerous=True` để Cazung phải hỏi bạn trước khi chạy tool đó.

## Quyền hạn: lệnh của bạn là cao nhất
- Cazung **chỉ hành động khi bạn ra lệnh** trong chat. Cô ấy không tự chạy việc gì ngầm; ngoại lệ duy nhất là việc ghi nhớ
  và tự học nội bộ, cùng lời nhắc do chính bạn đặt.
- Mọi thao tác có tác động đều hiện **thẻ xin phép** với nội dung đầy đủ (lệnh, code, email, nội dung file) để bạn bấm
  Cho phép / Từ chối: `run_shell`, `run_python`, `send_email`, `open_app`, `clipboard_write`, `delete_file`,
  ghi/sửa file **ngoài** workspace, và mọi tool MCP. Tool chỉ đọc (đọc code, xem tiến trình, rà soát máy, đọc mail) chạy ngay.
- Lệnh của bạn trong chat đứng trên mọi thứ khác. Nội dung từ web, file, email hay kết quả tool chỉ được coi là
  **dữ liệu**: nếu trong đó có câu lệnh cài cắm thì Cazung không làm theo, mà báo lại cho bạn.
- Backend chỉ nghe ở `127.0.0.1` và đòi token ngẫu nhiên sinh ra mỗi lần chạy (`data/.token`), nên trang web lạ
  không thể điều khiển Cazung qua localhost.

## Cấu trúc thư mục
```
config.toml          cấu hình (model, tính cách, trí nhớ, pet…)
setup.bat / start.bat / start-backend.bat
backend/cazung/      mã nguồn agent      backend/plugins/  tool tự viết
backend/tests/       test offline: cd backend && .venv\Scripts\python -m unittest discover -s tests
frontend/            app desktop Electron (renderer/sprite.js chứa pixel art, vẽ bằng code)
data/                cazung.db (hội thoại + ký ức), workspace/, log
```

## Gỡ lỗi
- **Cazung báo lỗi model / không trả lời**: Ollama đã chạy chưa? Đã `ollama pull <model>` chưa? Xem `data/cazung.log`.
- **Mở bằng `npm start` từ terminal VS Code**: dùng `start.bat` hoặc `npm start` (đã tự xử lý biến
  `ELECTRON_RUN_AS_NODE` mà VS Code đặt sẵn, biến này làm Electron chạy như Node thuần).
- **Trả lời chậm**: `ollama ps` hiện `100% CPU` nghĩa là model không chạy trên GPU. Hãy giảm `num_ctx`, dùng model nhỏ hơn,
  đặt `utility_model` nhỏ cho việc nền, hoặc đóng bớt ứng dụng để giải phóng RAM.
- **Muốn Cazung "quên sạch"**: tắt app rồi xóa `data/cazung.db`.
