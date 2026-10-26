# Makefile Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cài đặt công cụ GNU Make và thiết lập file Makefile đa năng tại thư mục gốc dự án để chuẩn hóa các tác vụ chạy, build, phát triển hybrid, kiểm thử và kiểm tra chất lượng mã nguồn.

**Architecture:** Sử dụng GNU Make làm bộ điều phối tác vụ trung tâm. Các tác vụ vòng đời dịch vụ (`up`, `down`, `build`, `logs`) gọi trực tiếp tới `docker compose`. Các tác vụ kiểm tra chất lượng và kiểm thử (`test`, `lint`, `typecheck`, `check`) thực thi trực tiếp trên môi trường máy host thông qua công cụ chuyên biệt (`pytest`, `ruff`, `npm`) để đạt hiệu năng cao nhất, kèm theo cơ chế tự động hóa khởi tạo môi trường (`setup`).

**Tech Stack:** GNU Make, Docker Compose, Python 3.12 (pytest, ruff, venv), Node.js 22 (npm, Vite, TypeScript), Bash.

**Spec:** `docs/superpowers/specs/2026-10-26-makefile-tooling-design.md`

## Global Constraints
- Hệ điều hành host: Ubuntu 26.04 LTS.
- Cú pháp Makefile: Bắt buộc dùng ký tự Tab thực (ASCII 0x09) cho các dòng lệnh công thức (recipe lines), tuyệt đối không dùng khoảng trắng (spaces).
- Khai báo `.PHONY`: Tất cả các target phải được khai báo trong `.PHONY` để tránh xung đột với các tệp hoặc thư mục cùng tên.
- Không hard-code thông tin nhạy cảm: Tuân thủ quy định bảo mật trong `AGENTS.md` và `docs/ai/hooks.md`.
- Phạm vi kiểm thử mặc định: Lệnh test backend phải giữ cờ `-m "not integration"` như quy định trong `pytest.ini`.

## Review Focus
- Sự cố cú pháp Tab vs Spaces: Đảm bảo recipe lines sử dụng Tab thực để tránh lỗi `*** missing separator. Stop.`.
- Xử lý môi trường ảo Python: Khi chạy `make test-backend` hoặc `make lint-backend`, kiểm tra sự tồn tại của `backend/.venv`, nếu chưa có sẽ hướng dẫn hoặc fallback để không gặp lỗi `No such file or directory`.
- Tạo file `.env` an toàn: `make setup` hoặc các lệnh khởi động cần đảm bảo file `.env` được copy từ `.env.example` mà không ghi đè nếu file `.env` đã tồn tại trước đó.
- Lan truyền mã lỗi (Exit code propagation): Các target tổ hợp như `make check`, `make test`, `make lint` phải dừng ngay và trả về exit code lỗi nếu có một bước con thất bại.
- Định dạng trợ giúp `make help`: Parser phải trích xuất chính xác các target và comment định dạng `##` để hiển thị danh mục lệnh đẹp mắt.

---

### Task 1: Cài đặt và xác minh GNU Make trên máy Host

**Files:**
- Không tạo/sửa file mã nguồn.

**Interfaces:**
- Consumes: Quản lý gói hệ thống `apt`.
- Produces: Lệnh nhị phân `make` trong `$PATH`.

- [ ] **Step 1: Kiểm tra trạng thái hiện tại của make**
  Run: `which make || echo "not installed"`
  Expected: Báo trạng thái chưa cài đặt hoặc đường dẫn nếu đã có.

- [ ] **Step 2: Cài đặt make bằng apt**
  Run: `sudo apt update && sudo apt install -y make`
  Expected: Lệnh cài đặt thành công với exit code 0.

- [ ] **Step 3: Xác minh phiên bản make**
  Run: `make --version`
  Expected: Hiển thị thông tin GNU Make (ví dụ `GNU Make 4.x...`) và exit code 0.

---

### Task 2: Tạo tệp Makefile tại thư mục gốc

**Files:**
- Create: `Makefile`

**Interfaces:**
- Consumes: `docker compose`, `backend/.venv/bin/pytest`, `backend/.venv/bin/ruff`, `frontend/npm`, kịch bản Python `.cursor/hooks/selftest.py` và `.github/scripts/scan_repo.py`.
- Produces: Bộ lệnh `make <target>` sẵn sàng sử dụng.

- [ ] **Step 1: Viết test xác thực cú pháp và cấu trúc Makefile**
  Tạo test tạm thời hoặc chạy `make -n` (dry-run) để kiểm tra cú pháp của tất cả các target mà không thực sự thay đổi trạng thái dịch vụ:
  Run: `make -n help up down restart logs build dev-hybrid test-backend test-frontend test lint-backend format-backend typecheck-frontend lint check setup clean`
  Expected: Trước khi tạo Makefile, lệnh báo lỗi `No such file or directory` hoặc `No targets specified and no makefile found`.

- [ ] **Step 2: Tạo file `Makefile` với các nhóm target theo thiết kế**
  Tạo `Makefile` tại thư mục gốc với các nội dung:
  - Khai báo `.DEFAULT_GOAL := help`
  - Khai báo `.PHONY` bao gồm tất cả các target.
  - Nhóm Trợ giúp:
    - `help`: Dùng awk để đọc và hiển thị các dòng có `##` mô tả.
  - Nhóm Cài đặt hệ thống:
    - `install-make`: `sudo apt update && sudo apt install -y make`
  - Nhóm Môi trường & Khởi tạo:
    - `setup`: Kiểm tra tạo `.env`, tạo venv backend `python3 -m venv backend/.venv`, cài `backend/.venv/bin/pip install -e ".[dev]"`, và cài `npm ci` trong thư mục `frontend/`.
    - `clean`: Dọn dẹp cache `find . -type d -name "__pycache__" -exec rm -rf {} +`, `.pytest_cache`, `.ruff_cache`, `.coverage`.
  - Nhóm Vòng đời Docker Compose:
    - `up`: `docker compose up -d`
    - `up-build`: `docker compose up -d --build`
    - `down`: `docker compose down`
    - `restart`: `docker compose restart`
    - `logs`: `docker compose logs -f`
    - `build`: `docker compose build`
  - Nhóm Phát triển kết hợp (Hybrid Dev):
    - `dev-hybrid`: Bật `docker compose up -d redis api worker` rồi chạy `cd frontend && npm run dev`.
  - Nhóm Kiểm thử & Chất lượng (Host-first):
    - `test-backend`: Chạy `pytest -m "not integration"` (dùng `backend/.venv/bin/pytest` nếu có, fallback `pytest`).
    - `test-frontend`: `cd frontend && npm test`.
    - `test`: Phụ thuộc vào `test-backend` và `test-frontend`.
    - `lint-backend`: Chạy `ruff check .` và `ruff format --check .` (dùng venv).
    - `format-backend`: Chạy `ruff format .` (dùng venv).
    - `typecheck-frontend`: `cd frontend && npm run typecheck`.
    - `lint`: Phụ thuộc vào `lint-backend` và `typecheck-frontend`.
    - `check`: Chạy lần lượt `lint`, `test`, `python3 .cursor/hooks/selftest.py`, `python3 .github/scripts/scan_repo.py`.

- [ ] **Step 3: Xác minh cú pháp Makefile không bị lỗi Tab và parse đúng**
  Run: `make -n check`
  Expected: Hiển thị các câu lệnh dự kiến của `check` và exit code 0 (không báo `missing separator`).

- [ ] **Step 4: Kiểm tra hiển thị `make help`**
  Run: `make help`
  Expected: Danh sách các lệnh hiển thị rõ ràng, kèm giải thích theo từng nhóm.

---

### Task 3: Cập nhật tài liệu README và Kiểm tra chính sách Repo

**Files:**
- Modify: `README.md`
- Test: `.cursor/hooks/selftest.py`, `.github/scripts/scan_repo.py`

**Interfaces:**
- Consumes: `Makefile` đã hoàn thiện.
- Produces: Tài liệu `README.md` hướng dẫn sử dụng `make`, đảm bảo toàn bộ chính sách repo xanh.

- [ ] **Step 1: Bổ sung mục Hướng dẫn Sử dụng Makefile vào `README.md`**
  Thêm đoạn ngắn gọn trong phần hướng dẫn chạy của `README.md` giới thiệu các lệnh `make up`, `make dev-hybrid`, `make test`, `make check`.

- [ ] **Step 2: Chạy kiểm tra chính sách và bảo mật repo**
  Run: `python3 .cursor/hooks/selftest.py && python3 .github/scripts/scan_repo.py`
  Expected: Toàn bộ kiểm tra chính sách, bí mật và hook đều PASS.

- [ ] **Step 3: Commit các thay đổi**
  ```bash
  git add Makefile README.md docs/superpowers/
  git commit -m "feat: add Makefile and developer tooling commands"
  ```
