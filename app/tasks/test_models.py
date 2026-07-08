from google import genai
from app.config import settings

client = genai.Client(api_key=settings.GEMINI_API_KEY)

response = client.models.embed_content(
    model="gemini-embedding-001",
    contents=["Hello world"]
)

print(response)