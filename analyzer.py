import base64
import json
from openai import OpenAI
import config

# Initialize OpenAI client
client = OpenAI(api_key=config.OPENAI_API_KEY)

EVALUATION_PROMPT = """
You are an expert Quality Assurance specialist for sales calls. Your task is to analyze the audio of a sales call and evaluate the manager's performance based on the specific criteria below.

IMPORTANT:
1.  **Strict Scoring**: For each category, you MUST assign one of the specific ALLOWED SCORES listed. Do NOT assign intermediate scores (e.g., do not give 8 if only 0, 5, 10 are allowed).
2.  **Manager Name**: Extract the manager's name from the audio (usually at the start). If not found, return "Unknown".
3.  **Transcription**: Provide a verbatim transcription of the call in Russian.

### EVALUATION CRITERIA:

1. **Greeting** (Max 10 points)
   *   **10 points**: Greeting according to script: manager names themselves, the company, and asks for client's name if unknown.
   *   **5 points**: Incomplete greeting: missing one element (manager name, title, or company), or failed to ask client's name.
   *   **0 points**: No greeting, no company name, or no request for client's name.
   *   **ALLOWED SCORES**: 0, 5, 10

2. **Needs Analysis** (Max 20 points)
   *   **20 points**: Manager asks open, closed, clarifying questions per script, waits for answers, uses active listening.
   *   **10 points**: Not all script questions asked; does not wait for answers; lacks active listening.
   *   **0 points**: Needs analysis absent; no relevant questions asked.
   *   **ALLOWED SCORES**: 0, 10, 20

3. **Presentation** (Max 20 points)
   *   **20 points**: Follows partner requirements. Offer based on needs, not overloaded, uses FAB (Features-Advantages-Benefits) technique.
   *   **10 points**: Partial script presentation, not based on FAB.
   *   **0 points**: No presentation. Merely stating product availability or company description is NOT a presentation.
   *   **"n/a"**: No objective need for presentation in conversation.
   *   **ALLOWED SCORES**: 0, 10, 20, "n/a"

4. **Closing** (Max 10 points)
   *   **10 points**: Uses closing phrases linked to specific action and timeframe.
   *   **5 points**: Only farewell phrases without deadlines or specific actions.
   *   **0 points**: No closing/Call to Action phrases and no farewell.
   *   **"n/a"**: Connection lost.
   *   **ALLOWED SCORES**: 0, 5, 10, "n/a"

5. **Summary & Next Steps** (Max 10 points)
   *   **10 points**: Summarized communication, voiced all agreements and next steps agreed upon.
   *   **5 points**: Only one action: either summarized OR voiced next step.
   *   **0 points**: No summary, just said goodbye.
   *   **"n/a"**: Connection lost.
   *   **ALLOWED SCORES**: 0, 5, 10, "n/a"

6. **Objection Handling** (Max 20 points)
   *   **20 points**: Handled ALL objections using algorithm: Join (conditional agreement) -> Clarifying questions -> FAB arguments -> Call to action.
   *   **10 points**: Handling not by algorithm: missing steps, weak arguments, or incomplete handling.
   *   **0 points**: No objection handling attempted despite objections present.
   *   **"n/a"**: No objections raised by client.
   *   **ALLOWED SCORES**: 0, 10, 20, "n/a"

7. **Speech Quality** (Max 10 points)
   *   **10 points**: No errors OR one minor error (e.g., single use of "just a sec").
   *   **5 points**: Several errors (2-3) or one critical error (filler words, lack of confidence, etc.).
   *   **0 points**: Many significant errors (>3), such as: unclear diction, lack of empathy, monotone, awkward pauses, filler words, negative tone, specific dialect errors, excessive diminutives.
   *   **ALLOWED SCORES**: 0, 5, 10

### OUTPUT FORMAT (JSON ONLY):
{
  "manager_name": "Name or Unknown",
  "transcription_text": "Full transcription...",
  "greeting_score": 10,
  "greeting_comment": "Explanation...",
  "needs_analysis_score": 20,
  "needs_analysis_comment": "Explanation...",
  "presentation_score": "n/a",
  "presentation_comment": "Reason...",
  "closing_score": 5,
  "closing_comment": "Explanation...",
  "summary_score": 10,
  "summary_comment": "Explanation...",
  "objection_handling_score": 20,
  "objection_handling_comment": "Explanation...",
  "speech_score": 5,
  "speech_comment": "Explanation...",
  "total_score": 75,
  "summary_text": "General conclusion and recommendations."
}
"""

def encode_audio(audio_path):
    with open(audio_path, "rb") as audio_file:
        return base64.b64encode(audio_file.read()).decode('utf-8')

import logging

def analyze_call_audio(audio_path, audio_format="mp3"):
    """
    Analyzes audio file directly using GPT-4o-audio-preview.
    """
    try:
        base64_audio = encode_audio(audio_path)
        
        logging.info(f"Sending audio to OpenAI. Path: {audio_path}, Format: {audio_format}, Size: {len(base64_audio)} bytes base64")

        response = client.chat.completions.create(
            model="gpt-4o-audio-preview", 
            modalities=["text"],
            messages=[
                {
                    "role": "system", 
                    "content": EVALUATION_PROMPT + "\nВАЖНО: Верни ТОЛЬКО чистый JSON без markdown форматирования (без ```json)."
                },
                {
                    "role": "user",
                    "content": [
                        { 
                            "type": "text", 
                            "text": "Проанализируй этот звонок." 
                        },
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64_audio,
                                "format": audio_format
                            }
                        }
                    ]
                }
            ]
        )
        
        content = response.choices[0].message.content
        logging.info("OpenAI response received.")
        
        # Clean up potential markdown code blocks
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "")
        elif content.startswith("```"):
            content = content.replace("```", "")
            
        content = content.strip()

        data = json.loads(content)

        # Recalculate total_score in case GPT summed incorrectly
        scores = [
            data.get('greeting_score', 0),
            data.get('needs_analysis_score', 0),
            data.get('speech_score', 0)
        ]
        for key in ['presentation_score', 'closing_score', 'summary_score', 'objection_handling_score']:
            score = data.get(key)
            if score != "n/a" and score is not None:
                scores.append(int(score))
        data['total_score'] = sum(scores)

        return data
        
    except Exception as e:
        logging.error(f"CRITICAL ERROR in analyze_call_audio: {type(e).__name__}: {e}", exc_info=True)
        return None
