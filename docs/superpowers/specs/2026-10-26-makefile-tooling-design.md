# Thiết kế Makefile và Cài đặt Công cụ cho Dự án (Makefile Tooling Design)

- **Ngày tạo:** 2026-10-26
- **Trạng thái:** Chờ phê duyệt (Pending Approval)
- **Quy mô:** Thay đổi nhỏ (Small change - Tiện ích nhà phát triển)

---

## 1. Mục đích và Động lực (Intent & Why)
Dự án câu chuyện đa ngữ sang sách nói (Story to Audiobook) hiện có nhiều thành phần: Docker Compose điều phối 4 service (`api`, `worker`, `redis`, `frontend`), môi trường Python backend với venv/ruff/pytest, môi trường Node/React frontend với npm/Vite, và các kịch bản kiểm tra chính sách.

Hiện tại, việc gõ thủ công các câu lệnh dài (hoặc phải nhớ nhiều cú pháp khác nhau giữa host và container) gây mất thời gian và dễ nhầm lẫn. Việc trang bị `make` và một file `Makefile` chuẩn hóa tại thư mục gốc giúp lập trình viên thao tác build, run, dev, test, lint và setup một cách nhanh chóng, đồng nhất.

---

## 2. Mục tiêu, Phi mục tiêu & Rào cản (Goals, Non-Goals, Anti-Goals)

### Mục tiêu (Goals)
1. Cung cấp hướng dẫn / lệnh cài đặt `make` trên môi trường Ubuntu 26.04 LTS (`sudo apt update && sudo apt install -y make`).
2. Xây dựng file `Makefile` tại thư mục gốc với cú pháp chuẩn, cấu trúc nhóm lệnh rõ ràng, hỗ trợ tự động hiển thị trợ giúp (`make help`).
3. Quản lý vòng đời dịch vụ Docker Compose:
   - `make up`: Khởi động toàn bộ stack ngầm (`docker compose up -d`).
   - `make down`: Dừng toàn bộ stack (`docker compose down`).
   - `make restart`: Khởi động lại stack.
   - `make logs`: Xem log theo thời gian thực (`docker compose logs -f`).
   - `make build`: Build lại toàn bộ docker images (`docker compose build`).
4. Hỗ trợ luồng phát triển kết hợp (Hybrid dev):
   - `make dev-hybrid`: Bật các service backend trong container (`redis`, `api`, `worker`) và chạy frontend Vite dev server trên máy host để hỗ trợ HMR tức thì.
5. Quản lý kiểm thử và chất lượng mã nguồn:
   - `make test`: Chạy toàn bộ unit test (`test-backend` và `test-frontend`).
   - `make test-backend`: Chạy `pytest -m "not integration"` (dùng venv).
   - `make test-frontend`: Chạy `npm test` trong `frontend/`.
   - `make lint`: Chạy linter backend (`ruff check`, `ruff format --check`) và typecheck frontend (`npm run typecheck`).
   - `make format`: Tự động format mã nguồn backend (`ruff format .`).
   - `make check`: Bộ kiểm tra toàn diện trước khi commit/push (lint, typecheck, unit test, `selftest.py`, `scan_repo.py`).
6. Khởi tạo và bảo trì môi trường:
   - `make setup`: Khởi tạo file `.env` từ `.env.example` (nếu chưa có), thiết lập Python venv + cài `dev` dependencies cho backend, và cài `npm ci` cho frontend.
   - `make clean`: Xóa các file rác, cache (`__pycache__`, `.pytest_cache`, `.ruff_cache`, build artifacts).

### Phi mục tiêu (Non-Goals)
- Không can thiệp hoặc thay đổi logic nghiệp vụ backend, frontend, cấu hình `docker-compose.yml`, hay quy trình CI hiện tại trong `.github/workflows/ci.yml`.
- Không thay đổi các quy định kiến trúc trong `AGENTS.md` hay `.cursor/rules/`.

### Rào cản & Điều không mong muốn (Anti-Goals)
- Không bắt buộc mọi tác vụ test/lint phải chạy qua container làm giảm tốc độ phát triển hàng ngày.
- Không hard-code các giá trị bí mật hay biến môi trường nhạy cảm vào Makefile.

---

## 3. Các ràng buộc kỹ thuật (Constraints)
- Hệ điều hành host: **Ubuntu 26.04 LTS**.
- Cú pháp Makefile: Bắt buộc dùng ký tự Tab thực (không dùng space cho indent công thức), khai báo `.PHONY` đầy đủ để tránh xung đột tên tệp.
- Xử lý venv thông minh: Tự động dùng `backend/.venv/bin/python` / `pytest` / `ruff` nếu tồn tại, hoặc fallback linh hoạt.

---

## 4. Các hướng tiếp cận đã xem xét (Approaches)

### Hướng tiếp cận 1: Phân tách thực tế (Docker cho Run/Build, Host cho Test/Lint)
- *Mô tả:* Docker quản lý vòng đời chạy dịch vụ; host quản lý test/lint.
- *Đánh đổi:* Tốc độ test/lint nhanh nhưng thiếu các target hỗ trợ setup môi trường tự động ban đầu.

### Hướng tiếp cận 2: Thuần Container (Container-only)
- *Mô tả:* Chạy toàn bộ mọi thứ (kể cả test/lint) bên trong container qua `docker compose run`.
- *Đánh đổi:* Máy host không cần môi trường ngôn ngữ, nhưng tốc độ phản hồi rất chậm, khó gắn debugger khi viết code.

### Hướng tiếp cận 3: Đa năng tích hợp (Lựa chọn được duyệt)
- *Mô tả:* Kết hợp quản lý vòng đời Docker Compose, chạy test/lint tốc độ cao trên host, bổ sung `make setup` chuẩn bị môi trường toàn diện và `make check` xác thực trước khi commit.
- *Lý do chọn:* Đáp ứng trải nghiệm lập trình tốt nhất cho cả người mới bắt đầu lẫn chu trình phát triển hàng ngày.

---

## 5. Danh mục Target chi tiết trong Makefile

| Target | Mô tả công việc | Lệnh thực thi tương ứng |
| :--- | :--- | :--- |
| `help` | Hiển thị hướng dẫn và danh sách lệnh | Script grep parsing tự động từ Makefile |
| `install-make` | Cài đặt công cụ make trên Ubuntu host | `sudo apt update && sudo apt install -y make` |
| `setup` | Chuẩn bị đầy đủ môi trường ban đầu | `cp -n .env.example .env`, tạo `backend/.venv`, `pip install -e ".[dev]"`, `npm ci` |
| `up` | Khởi động toàn bộ stack ngầm | `docker compose up -d` |
| `up-build` | Build lại và khởi động toàn bộ stack | `docker compose up -d --build` |
| `down` | Dừng và hạ toàn bộ container | `docker compose down` |
| `restart` | Khởi động lại stack | `docker compose restart` |
| `logs` | Xem log tất cả các container | `docker compose logs -f` |
| `build` | Build toàn bộ Docker images | `docker compose build` |
| `dev-hybrid` | Chế độ Hybrid dev (Backend docker + Frontend host) | `docker compose up -d redis api worker && cd frontend && npm run dev` |
| `test-backend` | Chạy unit test backend | `backend/.venv/bin/pytest -m "not integration"` |
| `test-frontend` | Chạy unit test frontend | `cd frontend && npm test` |
| `test` | Chạy toàn bộ unit test (backend + frontend) | `make test-backend && make test-frontend` |
| `lint-backend` | Kiểm tra cú pháp và định dạng backend | `backend/.venv/bin/ruff check . && backend/.venv/bin/ruff format --check .` |
| `format-backend`| Tự động format mã nguồn backend | `backend/.venv/bin/ruff format .` |
| `typecheck-frontend` | Kiểm tra kiểu TypeScript frontend | `cd frontend && npm run typecheck` |
| `lint` | Kiểm tra toàn bộ mã nguồn | `make lint-backend && make typecheck-frontend` |
| `check` | Kiểm tra toàn diện trước khi commit/push | `make lint && make test && python3 .cursor/hooks/selftest.py && python3 .github/scripts/scan_repo.py` |
| `clean` | Dọn dẹp cache và file tạm | Xóa `.pytest_cache`, `.ruff_cache`, `__pycache__`, `.coverage` |

---

## 6. Phần việc còn lại cho người thực thi (What's left to the builder)
1. Cài đặt `make` trên máy bằng `sudo apt update && sudo apt install -y make`.
2. Tạo tệp `Makefile` tại thư mục gốc với các target đã định nghĩa, đảm bảo thụt đầu dòng bằng Tab chuẩn.
3. Chạy thử nghiệm `make help` và kiểm tra hoạt động của các target cơ bản.
