# Requirements — Multilingual Story → AI Audiobook Generator

## 1. Overview

Xây dựng một Web tool cho phép người dùng cung cấp nội dung văn bản bằng một ngôn ngữ bất kỳ và chỉ định ngôn ngữ đầu ra, hệ thống tự động:

```text
Chinese Story
     ↓
Text Processing
     ↓
Chinese → Vietnamese Translation
     ↓
Vietnamese Narration Script
     ↓
Vietnamese TTS
     ↓
Audio Processing
     ↓
MP3 / WAV
```

Mục tiêu là tạo audio bằng ngôn ngữ do user chỉ định, với phong cách kể chuyện tự nhiên, phù hợp với các video kể chuyện trên YouTube/TikTok.

---

## 2. Goals

### Functional goals

Hệ thống phải có khả năng:

1. Nhận story tiếng Trung.
2. Tự động chia story thành các đoạn phù hợp.
3. Dịch tiếng Trung → tiếng Việt.
4. Chuyển bản dịch thành narration script tự nhiên.
5. Generate Vietnamese speech.
6. Ghép các audio segment thành một file audio hoàn chỉnh.
7. Export MP3/WAV.
8. Cho phép chọn Vietnamese voice.
9. Cho phép điều chỉnh tốc độ đọc.
10. Có thể xử lý story dài.
11. Có khả năng chạy bằng CPU, không bắt buộc GPU.
12. Ưu tiên sử dụng free/open-source tools.

### Non-functional goals

Ưu tiên:

1. Accuracy
2. Naturalness
3. Stability
4. Scalability
5. Maintainability
6. Performance
7. Cost

---

## 3. Target Use Case

Ví dụ user upload một story bằng bất kỳ ngôn ngữ nào:

```text
story.txt
```

User phải có thể chỉ định:

```text
Source language:
Auto Detect hoặc ngôn ngữ cụ thể

Target language:
Ngôn ngữ do user chọn

Voice:
Voice phù hợp với target language

Speed:
1.0x

Output:
MP3
```

Ví dụ một request có thể là:

```text
Chinese → Vietnamese
Chinese → English
Japanese → Vietnamese
English → Japanese
Korean → Vietnamese
Vietnamese → English
```

Hệ thống không được hard-code source language hoặc target language.

System xử lý:

```text
Chinese
    ↓
Translation
    ↓
Vietnamese
    ↓
Narration processing
    ↓
TTS
    ↓
MP3
```

Output:

```text
story_vi.mp3
```

---

## 4. Input Requirements

### 4.1 Supported input

MVP:

- `.txt`

Input text có thể thuộc bất kỳ ngôn ngữ nào được translation/TTS provider hỗ trợ.

Future:

- `.epub`
- `.docx`
- `.pdf`
- Paste text trực tiếp
- URL

### Text input

Cho phép user paste trực tiếp hoặc upload file.

---

## 5. Story Processing

Không được gửi toàn bộ truyện vào translation/TTS một lần.

Pipeline:

```text
Story
 ↓
Chapter Detection
 ↓
Paragraph Detection
 ↓
Sentence Detection
 ↓
Chunking
```

Mỗi chunk phải có giới hạn character/token phù hợp để tránh:

- API limit
- model context limit
- TTS timeout
- translation lỗi
- memory overflow

---

## 6. Translation Requirements

### 6.1 Source and target language

Source language và target language **không được cố định**.

User phải cung cấp hoặc chọn:

```text
source_language
 target_language
```

Trong đó:

- `source_language`: ngôn ngữ của input text hoặc `auto` để hệ thống tự detect.
- `target_language`: ngôn ngữ mà user muốn tạo narration/audio.

Ví dụ:

```text
source_language = zh
 target_language = vi
```

hoặc:

```text
source_language = ja
 target_language = en
```

Translation phải ưu tiên **natural language của target language**, không dịch word-by-word.

Ví dụ:

Bad:

```text
Anh nhìn cô gái trước mắt, im lặng trong một thời gian rất dài.
```

Better:

```text
Anh nhìn cô gái trước mặt, im lặng hồi lâu.
```

---

## 7. Translation Engine

Ưu tiên các solution miễn phí/open-source.

### Candidate 1 — NLLB-200

Ưu tiên cho MVP nếu chạy local.

```text
Source Language
   ↓
NLLB-200
   ↓
Target Language
```

NLLB implementation phải nhận `source_language` và `target_language` từ job/config, không hard-code cặp ngôn ngữ.

### Candidate 2 — LibreTranslate

Có thể dùng khi cần HTTP API architecture.

### Candidate 3 — LLM

Có thể hỗ trợ tùy chọn:

- OpenAI
- Gemini
- Local LLM
- Ollama

Không bắt buộc cho MVP.

Architecture phải abstraction translation provider:

```text
TranslationProvider
       │
       ├── NLLBProvider
       ├── LibreTranslateProvider
       ├── OpenAIProvider
       └── CustomProvider
```

---

## 8. Narration Script Processing

Đây là requirement quan trọng.

Translation output không nên trực tiếp đưa vào TTS.

Pipeline:

```text
Translated Text
      ↓
Narration Processor
      ↓
TTS-ready Script
```

Processor cần xử lý:

- punctuation
- sentence boundaries
- paragraph boundaries
- pause
- dialogue
- emphasis
- numbers
- abbreviations
- unusual symbols
- excessive punctuation
- quotation marks

Ví dụ:

Input:

```text
Anh nhìn cô gái trước mặt rồi im lặng rất lâu sau đó anh mới nói.
```

Có thể transform:

```text
Anh nhìn cô gái trước mặt...

Im lặng rất lâu.

Rồi cuối cùng, anh mới lên tiếng.
```

Mục tiêu là tạo rhythm giống narration, không đơn thuần đọc raw translated text.

---

## 9. TTS Requirements

### 9.1 Primary requirement

Output speech phải sử dụng **target language do user chỉ định**.

Ví dụ:

```text
Source: Chinese
Target: Vietnamese
→ Vietnamese speech
```

hoặc:

```text
Source: English
Target: Japanese
→ Japanese speech
```

TTS voice phải tương thích với target language.

với giọng:

- natural
- clear
- phù hợp kể chuyện
- không robotic nếu có thể

---

## 10. TTS Provider Strategy

TTS phải được abstraction:

```text
TTSProvider
    │
    ├── EdgeTTSProvider
    ├── PiperProvider
    ├── XTTSProvider
    ├── ChatterboxProvider
    └── ElevenLabsProvider
```

Cho phép thay provider mà không thay đổi business logic.

---

## 11. Edge TTS

### MVP recommended provider

**Edge TTS**

Lý do:

- không cần API key
- dễ integrate
- có nhiều multilingual voices
- neural TTS
- chất lượng khá tốt
- phù hợp prototype/MVP
- chi phí TTS gần như bằng 0 cho use case cá nhân

Ví dụ conceptual API:

```python
communicate = edge_tts.Communicate(
    text,
    voice=selected_voice
)

await communicate.save("output.mp3")
```

`selected_voice` phải được chọn dựa trên `target_language` và cấu hình của user, không hard-code Vietnamese voice.

System phải hỗ trợ nếu provider cho phép:

- Voice selection
- Speed
- Pitch
- Volume

---

## 12. Important Edge TTS Constraint

Không coi Edge TTS là:

```text
Official unlimited commercial API
```

System phải thiết kế provider abstraction để sau này có thể chuyển sang:

- ElevenLabs
- Azure
- Google Cloud
- Local TTS

mà không phải rewrite application.

---

## 13. Alternative TTS — Local

### Piper

Ưu tiên khi yêu cầu:

```text
CPU
16GB RAM
No GPU
```

Pros:

- Lightweight
- Fast
- Offline
- Open-source

Cons:

- Voice naturalness có thể thấp hơn premium TTS.

### XTTS / Chatterbox

Dùng khi cần:

- higher quality
- voice cloning
- expressive narration

Phải đánh giá:

- RAM
- VRAM
- CPU performance
- Vietnamese support
- Model license

Không mặc định yêu cầu GPU cho MVP.

---

## 14. Voice Quality Requirement

Mục tiêu không chỉ là:

> Text được đọc thành tiếng.

Mà là:

> Audio nghe giống một narrator kể chuyện.

Quality criteria:

### Pronunciation

- Vietnamese pronunciation rõ
- không nuốt chữ
- không đọc sai punctuation

### Rhythm

- pause hợp lý
- không đọc đều đều

### Speed

Default:

```text
1.0x
```

Có thể configure:

```text
0.8x
0.9x
1.0x
1.1x
1.2x
```

### Emotion

Nếu provider hỗ trợ:

- Neutral
- Storytelling
- Dramatic
- Calm

---

## 15. Audio Processing

Sau khi TTS:

```text
chunk_001.mp3
chunk_002.mp3
chunk_003.mp3
...
```

Dùng FFmpeg để merge:

```text
chunk_001
    +
chunk_002
    +
chunk_003
    ↓
story.mp3
```

System phải đảm bảo:

- không gap bất thường
- không overlap
- consistent sample rate
- consistent bitrate
- consistent audio format

---

## 16. Output

MVP:

```text
MP3
WAV
```

Config:

```text
Format:
MP3

Bitrate:
128 / 192 / 256 / 320 kbps
```

Default:

```text
MP3
128/192 kbps
```

Không cần 320kbps nếu source TTS không có chất lượng tương ứng.

---

## 17. Web UI

Ưu tiên Web application.

### Main screen

```text
┌─────────────────────────────────────────┐
│ Multilingual Story → AI Audio           │
├─────────────────────────────────────────┤
│                                         │
│ Upload Story                            │
│ [ Choose File ]                         │
│                                         │
│ OR                                      │
│                                         │
│ [ Paste Story Text................... ] │
│                                         │
├─────────────────────────────────────────┤
│ Source Language                         │
│ [ Auto Detect ▼ ]                       │
│                                         │
│ Target Language                         │
│ [ Vietnamese ▼ ]                        │
│                                         │
│ Voice                                   │
│ [ Vietnamese Male ▼ ]                   │
│                                         │
│ Speed                                   │
│ [──────●────] 1.0x                      │
│                                         │
│ Output                                  │
│ [ MP3 ▼ ]                               │
│                                         │
│             [ Generate Audio ]          │
└─────────────────────────────────────────┘
```

---

## 18. Processing UI

Trong quá trình generate:

```text
Processing...

✓ Reading story
✓ Splitting chapters
✓ Splitting text
✓ Translating chunk 12/35
✓ Preparing narration
→ Generating audio 8/35
○ Merging audio
```

Progress bar phải hiển thị tiến độ.

---

## 19. Job Architecture

Không xử lý request synchronously nếu story dài.

Không làm:

```text
POST /generate

→ wait 30 minutes
→ response
```

Nên:

```text
POST /jobs
     ↓
Job ID
     ↓
Queue
     ↓
Worker
     ↓
Translation
     ↓
TTS
     ↓
FFmpeg
     ↓
Completed
```

API:

```http
POST /api/jobs
```

Response:

```json
{
  "job_id": "uuid",
  "status": "queued"
}
```

Status:

```http
GET /api/jobs/{id}
```

---

## 20. Job State Machine

```text
QUEUED
  ↓
PARSING
  ↓
TRANSLATING
  ↓
PREPARING_TTS
  ↓
GENERATING_AUDIO
  ↓
MERGING
  ↓
COMPLETED
```

Error:

```text
ANY STATE
   ↓
FAILED
```

---

## 21. Retry

Mỗi chunk phải retry độc lập.

Ví dụ:

```text
chunk_001 ✓
chunk_002 ✓
chunk_003 ✗
chunk_004 ✓
```

Chỉ retry:

```text
chunk_003
```

Không generate lại toàn bộ story.

---

## 22. Caching

Translation và TTS nên có cache.

Ví dụ cache key:

```text
hash(
    text +
    language +
    provider +
    voice +
    settings
)
```

Nếu user generate lại cùng text/config:

```text
Cache HIT
```

Không gọi TTS/translation lại.

---

## 23. Storage

MVP:

```text
storage/
├── jobs/
│   └── {job_id}/
│       ├── source.txt
│       ├── translated.txt
│       ├── chunks/
│       │   ├── 001.txt
│       │   ├── 002.txt
│       │   └── ...
│       ├── audio/
│       │   ├── 001.mp3
│       │   ├── 002.mp3
│       │   └── ...
│       └── final.mp3
```

---

## 24. Suggested Tech Stack

### Backend

```text
Python
FastAPI
```

Lý do:

- TTS ecosystem tốt
- AI/ML ecosystem tốt
- async support
- dễ integrate FFmpeg
- dễ integrate NLLB/PyTorch

### Frontend

```text
React
TypeScript
```

hoặc Next.js.

### Queue

MVP:

```text
Redis
```

Worker:

```text
Celery
```

hoặc RQ.

### Audio

```text
FFmpeg
```

### Container

```text
Docker
Docker Compose
```

---

## 25. Recommended MVP Architecture

```text
                    ┌──────────────┐
                    │   React UI   │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   FastAPI    │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │    Redis     │
                    │    Queue     │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │    Worker    │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         Translator     Narrator      TTS
          NLLB-200      Processor   Edge TTS
              │                         │
              └──────────┬──────────────┘
                         ▼
                     FFmpeg
                         │
                         ▼
                    final.mp3
```

---

## 26. Provider Interfaces

AI coding agent phải implement interface thay vì hard-code provider.

### Translation

```python
class TranslationProvider(ABC):

    async def translate(
        self,
        text: str,
        source_language: str,
        target_language: str
    ) -> str:
        ...
```

### TTS

```python
class TTSProvider(ABC):

    async def synthesize(
        self,
        text: str,
        language: str,
        voice: str,
        settings: TTSSettings
    ) -> AudioResult:
        ...
```

---

## 27. Configuration

Không hard-code:

```env
TRANSLATION_PROVIDER=nllb
TTS_PROVIDER=edge
DEFAULT_SOURCE_LANGUAGE=zh
DEFAULT_TARGET_LANGUAGE=vi
DEFAULT_VOICE=vi-VN-NamMinhNeural
```

Future:

```env
TTS_PROVIDER=elevenlabs
```

hoặc:

```env
TTS_PROVIDER=xtts
```

mà không thay đổi application logic.

---

## 28. Logging

Log phải có:

```text
job_id
chunk_id
provider
processing_time
characters
status
error
retry_count
```

Ví dụ:

```text
INFO job=abc123 chunk=15
provider=edge_tts
characters=843
duration=12.4s
status=completed
```

Không log:

- API keys
- sensitive user content nếu không cần thiết

---

## 29. Error Handling

Các lỗi phải được phân loại:

```text
INVALID_INPUT
UNSUPPORTED_LANGUAGE
TRANSLATION_FAILED
TTS_FAILED
AUDIO_PROCESSING_FAILED
STORAGE_FAILED
PROVIDER_RATE_LIMIT
TIMEOUT
```

UI phải hiển thị lỗi dễ hiểu.

Không hiển thị stack trace cho user.

---

## 30. Long Story Requirement

Tool phải có khả năng xử lý:

```text
10,000 characters
50,000 characters
100,000+ characters
```

mà không crash.

Phải sử dụng:

- streaming/chunking
- queue
- incremental processing
- checkpoint
- cache

Không load toàn bộ audio vào RAM.

---

## 31. Resume Requirement

Nếu worker crash tại:

```text
chunk 37 / 100
```

sau khi restart:

```text
resume from chunk 37
```

Không generate lại chunk 1 → 36.

---

## 32. Audio Quality Analysis

Tool nên hỗ trợ sau này:

```text
Duration
Sample rate
Bitrate
Volume
Silence
```

Có thể dùng:

```text
FFprobe
FFmpeg
```

---

## 33. YouTube Voice Reference

User đã cung cấp một video YouTube làm reference voice:

```text
https://youtu.be/XT-pGRVpGz8
```

Mục tiêu không phải copy voice của creator, mà dùng video làm quality/style reference để đánh giá:

- Naturalness
- Narration speed
- Pause
- Pitch
- Tone
- Pronunciation
- Storytelling rhythm

### Audio reference workflow

User có thể download audio của video và upload vào system:

```text
reference.mp3
       ↓
Voice Analysis
       ↓
Voice characteristics
       ↓
TTS configuration
```

Tool không được tuyên bố chính xác provider nếu không có bằng chứng.

Không tự kết luận:

```text
"This is definitely ElevenLabs."
```

Chỉ đưa ra candidate dựa trên audio analysis.

---

## 34. Free-first Strategy

MVP phải ưu tiên:

### Translation

```text
NLLB-200
```

### TTS

```text
Edge TTS
```

### Audio

```text
FFmpeg
```

### Infrastructure

```text
Docker
Redis
FastAPI
React
```

Mục tiêu:

```text
$0 API cost
```

cho development/personal usage, trong phạm vi các dịch vụ free/không yêu cầu API key.

---

## 35. Future Provider

Architecture phải dễ mở rộng.

### Translation

```text
├── NLLB
├── LibreTranslate
├── Google
├── OpenAI
└── Gemini
```

### TTS

```text
├── Edge TTS
├── Piper
├── XTTS
├── Chatterbox
├── ElevenLabs
├── Azure
└── Google
```

---

## 36. Security Requirements

Nếu deploy public:

- File upload validation
- File size limit
- MIME validation
- Filename sanitization
- Path traversal prevention
- Rate limiting
- Job ownership
- Temporary file cleanup
- API authentication
- Never expose provider API keys
- Prevent arbitrary FFmpeg command injection

Không đưa filename/user input trực tiếp vào shell command.

---

## 37. MVP Scope

### Phase 1

```text
TXT upload
   ↓
Source Language → Target Language
   ↓
Edge TTS
   ↓
MP3
```

Không hard-code `Chinese → Vietnamese`; cặp ngôn ngữ phải do user cấu hình.

Có:

- React UI
- FastAPI
- Redis
- Worker
- NLLB
- Edge TTS
- FFmpeg
- Docker Compose

### Phase 2

Thêm:

- EPUB
- chapter detection
- voice selection
- speed
- pitch
- audio preview
- caching
- resume
- job history

### Phase 3

Thêm:

- XTTS
- Chatterbox
- voice cloning
- multiple speakers
- dialogue detection
- emotion
- subtitle generation

### Phase 4

```text
Story
 ↓
Translation
 ↓
Narration
 ↓
TTS
 ↓
Subtitle
 ↓
Images/video
 ↓
FFmpeg
 ↓
YouTube-ready video
```

---

## 38. Acceptance Criteria

MVP được xem là đạt khi:

### AC-01

User upload Chinese `.txt`.

→ System đọc được file.

### AC-02

System translate từ `source_language` sang `target_language` theo cấu hình của user.

### AC-03

Vietnamese text được TTS thành audio.

### AC-04

Audio sử dụng Vietnamese voice.

### AC-05

Output là MP3 playable bằng browser/VLC.

### AC-06

Story dài được chunk tự động và chunking không phụ thuộc source/target language.

### AC-07

Một chunk fail không làm mất toàn bộ job.

### AC-08

Job có thể resume.

### AC-09

System không yêu cầu GPU.

### AC-10

Toàn bộ MVP chạy bằng:

```bash
docker compose up
```

### AC-11

Có thể thay Edge TTS bằng provider khác mà không sửa business logic.

### AC-12

Generated audio phải có narration rhythm hợp lý, không đơn thuần đọc raw translated text.

---

## 39. Recommended Development Order

AI coding agent nên implement theo thứ tự:

```text
1. Project structure
       ↓
2. Docker Compose
       ↓
3. FastAPI
       ↓
4. React UI
       ↓
5. File upload
       ↓
6. Text parser
       ↓
7. Chunker
       ↓
8. TranslationProvider interface
       ↓
9. NLLB implementation
       ↓
10. TTSProvider interface
       ↓
11. Edge TTS implementation
       ↓
12. Audio storage
       ↓
13. FFmpeg merger
       ↓
14. Redis queue
       ↓
15. Worker
       ↓
16. Job status API
       ↓
17. Progress UI
       ↓
18. Retry
       ↓
19. Cache
       ↓
20. Resume
       ↓
21. Tests
```

---

## 40. Architecture Recommendation

Không đặt tên core architecture là "Chinese → Vietnamese TTS" hoặc gắn core logic với bất kỳ một language pair nào.

Nên thiết kế theo hướng:

```text
Text-to-Speech Localization Pipeline
```

Generic pipeline:

```text
Input Text
   ↓
Source Language Detection / User Selection
   ↓
Translation
   ↓
Target Language
   ↓
Narration Processing
   ↓
TTS Voice Selection
   ↓
Audio
```

Hệ thống phải hỗ trợ nhiều language pair tùy theo capability của translation/TTS providers.

Ví dụ:

```text
Chinese → Vietnamese
Chinese → English
Japanese → Vietnamese
Korean → Vietnamese
English → Vietnamese
Vietnamese → English
English → Japanese
Japanese → Korean
```

Không giới hạn danh sách trên và không được hard-code language pair trong business logic.

---

## 41. Final MVP Recommendation

MVP nên sử dụng:

```text
Frontend:
React + TypeScript

Backend:
Python + FastAPI

Queue:
Redis + Worker

Translation:
NLLB-200

TTS:
Edge TTS

Audio:
FFmpeg

Deployment:
Docker Compose
```

Language configuration:

```text
source_language: user-defined or auto-detect
target_language: user-defined
voice: automatically filtered by target language
```

Target environment:

```text
CPU
16GB RAM
No GPU required
```

Sau MVP, benchmark chất lượng Edge TTS với audio reference YouTube trước khi quyết định có cần chuyển sang XTTS, Chatterbox, ElevenLabs hoặc provider khác hay không.
