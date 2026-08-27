# Johnny Robot Community Edition

**An accessible AI tutor for community college learning labs**

Johnny Robot Community Edition is a production-ready, multi-user voice AI learning assistant. Students can ask questions via voice or text, upload course materials (up to 100MB), and get guided learning support across any subject. Built with academic integrity and WCAG 2.2 accessibility standards.

**Technology:** FastAPI, PocketBase, LiveKit, Google Gemini, Mem0

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![LiveKit](https://img.shields.io/badge/LiveKit-Agents-orange.svg)](https://docs.livekit.io/agents/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## About Johnny Robot Community Edition

Johnny Robot Community Edition is an AI learning assistant designed for community college learning labs. Using real-time voice interaction and document understanding, it helps students explore concepts, understand course materials, and build knowledge through guided questioning.

**Key Principles:**
- **Socratic Method:** Guides students to discover answers through questioning
- **Academic Integrity:** Never completes assignments; helps students learn to solve problems
- **Accessibility First:** WCAG 2.2 compliant for all learners
- **Multi-Subject:** Supports any discipline or course

---

## Features

### Voice & Text Tutoring
- **Voice Tutor** - Natural voice conversations powered by Google Gemini Realtime API
- **Text Tutor** - Chat interface with document context
- **Vision Support** - Share your screen or camera for visual help
- **Multi-Language** - 9 languages supported (English, Spanish, Vietnamese, French, German, Japanese, Korean, Chinese)

### Course Materials & RAG
- **Upload textbooks** - PDF, TXT, MD supported (up to 100MB)
- **Smart search** - Powered by Google Gemini File Search API
- **Semantic understanding** - AI understands context, not just keywords

### Canvas LMS Integration
- **Assignments & Events** - Ask "What's due this week?"
- **Announcements** - Stay updated with course news
- **Material Access** - Pull course files directly into the conversation

### Memory & Personalization
- **Learning memory** - Johnny Robot Community Edition remembers your progress (Mem0)
- **Language preference** - Saved across sessions
- **Individual sessions** - Each student gets personalized attention

---

## Architecture

```
┌─────────────┐
│   Frontend  │ (React/Vite)
└──────┬──────┘
       │
┌──────▼──────────────────┐
│    FastAPI Backend      │
│  - Auth (PocketBase)    │
│  - LiveKit Tokens       │
│  - Canvas Integration   │
│  - Textbook Management  │
└──────┬──────────────────┘
       │
       ├──────────────────┬─────────────────┐
       │                  │                 │
┌──────▼──────┐    ┌──────▼────┐    ┌──────▼──────┐
│ PocketBase  │    │  LiveKit  │    │   Gemini    │
│  - Auth     │    │  - Rooms  │    │  - Voice    │
│  - Records  │    │  - Agent  │    │  - RAG      │
└─────────────┘    └───────────┘    └─────────────┘
```

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Backend** | FastAPI | REST API & async support |
| **Database** | PocketBase | User data, metadata |
| **Auth** | PocketBase | Secure authentication |
| **Voice Agent** | Google Gemini Realtime | Natural voice & vision |
| **RAG/Search** | Google Gemini File Search | Document understanding |
| **Real-time** | LiveKit Cloud | WebRTC infrastructure |
| **Memory** | Mem0 | User conversation memory |
| **LMS** | Canvas API | Course data integration |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- Conda (recommended)
- A PocketBase instance (self-hosted alongside the API)
- LiveKit Cloud account
- Google AI API key
- Mem0 API key

### Setup

```bash
# Clone the repository
git clone https://github.com/your-org/johnnyrobot-community-edition.git
cd johnnyrobot-community-edition

# Create conda environment
conda create -n johnnyrobot-community-edition python=3.11 -y
conda activate johnnyrobot-community-edition

# Install Python dependencies
pip install -r requirements.txt

# Install frontend dependencies
cd frontend
npm install
cd ..

# Copy environment template and fill in your keys
cp .env.example .env
```

### Running Locally (3 Terminals)

**Terminal 1: Backend API**
```bash
conda activate johnnyrobot-community-edition
python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2: Voice Agent**
```bash
conda activate johnnyrobot-community-edition
python agent.py dev
```

**Terminal 3: Frontend**
```bash
cd frontend
npm run dev
```

**Terminal 4 (Optional): HTTPS Tunnel**
```bash
# Only needed for camera/screen sharing on network devices
cloudflared tunnel --url http://localhost:3000
```

### Access URLs

| URL | Camera/Screen | Access From |
|-----|---------------|-------------|
| `http://localhost:3000` | Works | Mac only |
| `http://YOUR_LOCAL_IP:3000` | Blocked | Local network |
| `https://xxx.trycloudflare.com` | Works | Any device |

---

## Environment Variables

Create a `.env` file with:

```env
# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxx
LIVEKIT_API_SECRET=secretxxxxxxxxxx

# Google Gemini (Voice + RAG)
GOOGLE_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxx

# OpenAI (Optional - alternative voice provider)
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# AI Provider Selection ("google" or "openai")
AI_PROVIDER=google

# Mem0 Memory
MEM0_API_KEY=mem0-xxxxxxxxxxxxxxxxxxxxxxxx

# Language (default)
AGENT_LANGUAGE=en-US

# Application
APP_SECRET_KEY=your-random-secret-key
ENVIRONMENT=development
DEBUG=true
```

---

## Deployment

Johnny Robot Community Edition supports Docker deployment on any VPS or container platform.

### Quick Docker Deploy (Production)

```bash
# Build and run all services with Caddy for automatic SSL
docker compose -f docker-compose.prod.yml up -d --build
```

---

## Contributing

Contributions are welcome! Please read our contributing guidelines before submitting PRs.

---

## License

MIT License - see [LICENSE](LICENSE) for details. Incorporated third-party material retains the terms listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

---

## Acknowledgments

- [LiveKit](https://livekit.io/) - Real-time voice infrastructure
- [Google Gemini](https://deepmind.google/technologies/gemini/) - AI models
- [PocketBase](https://pocketbase.io/) - Authentication & database
- [Mem0](https://mem0.ai/) - Conversation memory
