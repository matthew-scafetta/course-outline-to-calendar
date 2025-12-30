# Backend — Course Outline Parser API
FastAPI backend responsible for extracting, normalizing, and deduplicating academic events from course outlines.

## Responsibilities
- Accept raw text extracted from course outline PDFs
- Perform structured extraction of academic events
- Normalize inconsistent naming and formatting
- Deduplicate repeated events conservatively
- Merge event descriptions without data loss
- Return clean, calendar-ready JSON

## Key Design Principles
- **Conservative deduplication:** avoid deleting valid deliverables
- **Deterministic output:** post-process API-extracted data to ensure consistency
- **Separation of concerns:** parsing, normalization, and merge logic are isolated

## Tech Stack
- Python
- FastAPI
- OpenAI API (structured extraction)
- Pydantic (data validation)

## Environment Variables (OpenAI API Key)
This backend requires an OpenAI API key to perform structured extraction.

1. Inside the `backend/` directory, create a file named `.env`
2. Add your OpenAI API key in the following format:

```env
OPENAI_API_KEY=your_api_key_here
```

## Running the Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd app
python main.py
```