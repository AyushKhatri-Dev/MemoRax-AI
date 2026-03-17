"""
🧠 MemoRax AI - Core Memory Engine
LangChain + ChromaDB + Groq (Llama) powered personal memory assistant
"""
import uuid
import logging
import requests
from datetime import datetime, timedelta

from groq import Groq
from django.conf import settings
from django.utils import timezone as tz
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

from .models import BotUser, Memory, ConversationHistory, Reminder, CalendarEvent, SavedFile

logger = logging.getLogger(__name__)


class MemoRaxBrain:
    """
    The AI brain of MemoRax - handles:
    1. Saving memories (text → embedding → ChromaDB)
    2. Querying memories (semantic search + LLM response)
    3. Smart conversation with memory context
    4. Auto-tagging and categorization
    """

    def __init__(self):
        self.embeddings = HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2"
        )
        # Groq LLM for chat (fast, free)
        self.groq = Groq(api_key=settings.GROQ_API_KEY)

        self.vectorstore = Chroma(
            collection_name="memorax_memories",
            persist_directory=str(settings.CHROMA_PERSIST_DIR),
            embedding_function=self.embeddings
        )

    # ===========================
    # SAVE MEMORY
    # ===========================
    def save_memory(
        self,
        user: BotUser,
        content: str,
        source: str = "whatsapp",
        tags: list = None,
        media_path: str = None
    ) -> dict:
        """Save a new memory for the user"""

        if not user.can_save_memory():
            return {
                "success": False,
                "message": "⚠️ Memory limit reached! Upgrade to Pro for unlimited memories.\nType /upgrade for details."
            }

        # Generate unique ID
        chroma_id = f"mem_{user.phone}_{uuid.uuid4().hex[:12]}"

        # Auto-generate tags if not provided
        if not tags:
            tags = self._auto_tag(content)

        # Save to ChromaDB (vector store)
        self.vectorstore.add_texts(
            texts=[content],
            metadatas=[{
                "user_phone": user.phone,
                "source": source,
                "tags": ",".join(tags),
                "timestamp": datetime.now().isoformat(),
                "chroma_id": chroma_id
            }],
            ids=[chroma_id]
        )

        # Save metadata to Django DB
        Memory.objects.create(
            user=user,
            content_preview=content[:200],
            source=source,
            tags=tags,
            chroma_id=chroma_id,
            media_path=media_path  # Store image/file path
        )

        # Update user memory count
        user.memory_count += 1
        user.save()

        return {
            "success": True,
            "message": f"✅ Memory saved!\n📝 \"{content[:80]}{'...' if len(content) > 80 else ''}\"\n🏷️ Tags: {', '.join(tags)}"
        }

    # ===========================
    # QUERY MEMORIES
    # ===========================
    def query_memory(self, user: BotUser, query: str) -> str:
        """Search user's memories and generate AI response"""
        import re, pytz
        ist = pytz.timezone('Asia/Kolkata')

        # Semantic search in ChromaDB - only this user's memories
        try:
            results = self.vectorstore.similarity_search(
                query,
                k=settings.MAX_RETRIEVAL_RESULTS,
                filter={"user_phone": user.phone}
            )
        except Exception as e:
            logger.error(f"ChromaDB search error: {e}")
            results = []

        # --- Direct CalendarEvent DB search ---
        # Parse month/day from query so even old events (before ChromaDB sync) are found
        cal_context_parts = []
        try:
            q_lower = query.lower()
            month_map = {
                "jan": 1, "january": 1, "feb": 2, "february": 2,
                "mar": 3, "march": 3, "apr": 4, "april": 4,
                "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
                "aug": 8, "august": 8, "sep": 9, "september": 9,
                "oct": 10, "october": 10, "nov": 11, "november": 11,
                "dec": 12, "december": 12,
            }
            # Find mentioned month
            mentioned_month = None
            for name, num in month_map.items():
                if name in q_lower:
                    mentioned_month = num
                    break

            # Find mentioned day number
            day_match = re.search(r'\b(\d{1,2})\b', query)
            mentioned_day = int(day_match.group(1)) if day_match else None

            # Find mentioned year
            year_match = re.search(r'\b(202\d|203\d)\b', query)
            mentioned_year = int(year_match.group(1)) if year_match else None

            cal_qs = CalendarEvent.objects.filter(user=user)
            if mentioned_month:
                cal_qs = cal_qs.filter(start_time__month=mentioned_month)
            if mentioned_day:
                cal_qs = cal_qs.filter(start_time__day=mentioned_day)
            if mentioned_year:
                cal_qs = cal_qs.filter(start_time__year=mentioned_year)

            # Also keyword search on title/description if no date found
            if not mentioned_month and not mentioned_day:
                words = [w for w in q_lower.split() if len(w) > 3
                         and w not in {"have", "what", "when", "does", "any", "is", "the", "my",
                                       "kya", "hai", "meri", "mera", "mere"}]
                from django.db.models import Q
                q_filter = Q()
                for w in words:
                    q_filter |= Q(title__icontains=w) | Q(description__icontains=w)
                if q_filter:
                    cal_qs = cal_qs.filter(q_filter)

            for ev in cal_qs[:5]:
                local_start = ev.start_time.astimezone(ist)
                local_end = ev.end_time.astimezone(ist)
                participants = ", ".join(ev.participants) if ev.participants else ""
                cal_context_parts.append(
                    f"[Calendar Event] {ev.title} on "
                    f"{local_start.strftime('%d %B %Y (%A)')} "
                    f"{local_start.strftime('%I:%M %p')}–{local_end.strftime('%I:%M %p')}"
                    + (f" | Participants: {participants}" if participants else "")
                    + (f" | Location: {ev.location}" if ev.location else "")
                    + (f" | {ev.description}" if ev.description else "")
                )
        except Exception as e:
            logger.error(f"CalendarEvent DB query error: {e}")

        if not results and not cal_context_parts:
            return "🤔 No relevant memories found.\n\nTry saving some memories first!\nExample: /save Meeting with Rahul tomorrow at 3pm"

        # Build context from retrieved memories
        context_parts = []
        for i, doc in enumerate(results, 1):
            timestamp = doc.metadata.get("timestamp", "unknown")
            context_parts.append(f"Memory {i} (saved: {timestamp[:10]}):\n{doc.page_content}")

        # Merge calendar events + ChromaDB memories into one context
        all_parts = cal_context_parts + context_parts
        context = "\n\n".join(all_parts)

        # Get conversation history for multi-turn context
        recent_history = ConversationHistory.objects.filter(
            user=user
        ).order_by('-created_at')[:6]

        history_text = ""
        if recent_history:
            history_items = reversed(list(recent_history))
            history_text = "\n".join(
                [f"{'User' if h.role == 'user' else 'MemoRax'}: {h.content}" for h in history_items]
            )

        # Generate response using LLM
        system_prompt = f"""You are MemoRax AI, a personal memory assistant on WhatsApp.
The user is asking about something they previously saved. Answer from the memories provided.

Rules:
- Answer based on the user's saved memories below — even if the match is indirect or partial
- If the user asks "mujhe kya pasand hai" and memory says "mujhe gulaab jamoon pasand hai" → answer confidently
- If the user asks "kitni meetings hui" and memories have meetings → count and answer
- Reply in the SAME LANGUAGE the user wrote in (Hindi, English, or Hinglish)
- Be direct and concise (WhatsApp format)
- Format with line breaks, not long paragraphs
- If memory has dates/times, highlight them clearly
- Do NOT say "I don't have information about you" if relevant memories are shown below
- If truly nothing relevant is found in the memories, say "Maine is baare mein kuch save nahi kiya hai tumne"

User's Saved Memories:
{context}

Recent Conversation:
{history_text}"""

        try:
            response = self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            answer = response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq LLM error: {e}")
            answer = f"⚠️ Sorry, I had trouble processing that. Here's what I found:\n\n"
            for doc in results[:3]:
                answer += f"📌 {doc.page_content[:100]}...\n\n"

        # Save conversation history
        ConversationHistory.objects.create(user=user, role="user", content=query)
        ConversationHistory.objects.create(user=user, role="assistant", content=answer)

        return answer

    # ===========================
    # SMART CHAT (auto-save + respond)
    # ===========================
    def smart_chat(self, user: BotUser, message: str) -> str:
        """
        Intelligent chat - determines if user wants to:
        1. Save something (detects intent)
        2. Ask something (queries memories)
        3. Set a reminder
        4. Just chat (responds with context)
        Uses keyword detection first (fast, no LLM call) with LLM fallback.
        """
        # Check reminder acknowledgment FIRST (highest priority)
        if self._is_reminder_ack(message):
            return self.acknowledge_reminder(user)

        intent = self._detect_intent(message)

        if intent == "TODO":
            return self.handle_todo(user, message)
        elif intent == "SAVE":
            result = self.save_memory(user, message)
            return result["message"]
        elif intent == "QUERY":
            return self.query_memory(user, message)
        elif intent == "REMINDER":
            return self.parse_and_create_reminder(user, message)
        elif intent == "IMAGE_RETRIEVE":
            return self.retrieve_image(user, message)
        elif intent == "CALENDAR_EVENT":
            return self.create_calendar_event(user, message)
        else:
            return self._general_chat(user, message)

    def _detect_intent(self, message: str) -> str:
        """Fast keyword-based intent detection — no LLM call needed"""
        msg = message.lower().strip()

        # ── STEP 0: File/media send intent — checked BEFORE question patterns ──
        # "send my X", "bhejo meri X", "show me my photo", etc. → always IMAGE_RETRIEVE
        send_triggers = [
            "send my", "send me my", "send me the", "send the", "send photo", "send image", "send file", "send pdf",
            "bhejo meri", "bhejo mera", "bhejo mere", "bhejo", "mujhe bhejo", "mujhe de do", "mujhe do",
            "woh bhejo", "wapis bhejo", "wapas bhejo", "retrieve my", "retrieve the",
            "get my file", "get my photo", "get my image", "get my doc", "get the photo", "get the file",
            "show my", "show me my", "show photo", "show the photo", "show image", "show the image",
            "give me photo", "give me the photo", "give me image", "give my photo",
            "pull up", "fetch my", "fetch the", "find my", "find the photo", "find the image",
        ]
        
        # Check if it's a file request - also check for image/file words
        image_file_words = ["photo", "image", "file", "doc", "pdf", "picture", "pic", "vault", "attachment"]
        action_words = ["send", "show", "give", "get", "fetch", "pull", "retrieve", "bhejo", "dikhao", "dikha", "de do", "wapas"]
        
        has_action = any(action in msg for action in action_words)
        has_file_word = any(word in msg for word in image_file_words)
        
        # Direct trigger match
        for trigger in send_triggers:
            if trigger in msg:
                return "IMAGE_RETRIEVE"
        
        # Smart match: action word + file word = file request
        if has_action and has_file_word:
            return "IMAGE_RETRIEVE"

        # ── STEP 1: Question/retrieval indicators ──
        # These patterns mean user is ASKING about existing data → QUERY
        question_patterns = [
            # English questions
            "what was", "what is", "what are", "what were",
            "when is", "when was", "when are", "when were",
            "where is", "where was", "do i have", "did i", "have i",
            "how many", "how much", "how often", "tell me",
            "find my", "search for", "list my", "what do i",
            # Hindi/Hinglish questions
            "kya hai", "kya tha", "kya hain", "kya the",
            "kab hai", "kab tha", "kab hogi", "kab hoga",
            "kahan hai", "kahan tha", "kaun hai", "kaun tha",
            "kitni", "kitna", "kitne", "abtak", "ab tak",
            "mujhe kya", "mujhe batao", "mujhe bata",
            "meri kya", "mera kya", "mere kya",
            "koi hai kya", "hai kya", "hain kya", "hua hai kya",
            "hui hai kya", "hue hain", "hua tha", "hui thi",
            "batao", "bata do", "bata de", "bta do",
            "kaun si", "kaun sa", "konsi", "konsa",
            "mujhe kya pasand", "tumhe kya pasand", "usse kya pasand",
        ]
        for qp in question_patterns:
            if qp in msg:
                return "QUERY"

        # Questions ending with ? (but NOT if it's a file request)
        if msg.endswith("?") and not (has_action and has_file_word):
            return "QUERY"

        # ── STEP 2: IMAGE_RETRIEVE — broader keyword match ──
        image_retrieve_keywords = [
            "send that", "woh image", "woh photo", "that image", "that photo",
            "get my", "retrieve", "meri photo", "meri image", "mera photo",
            "meri file", "mera file", "wo file", "woh file",
        ]
        image_words = [
            # file types
            "image", "photo", "picture", "pic", "screenshot",
            "file", "pdf", "docx", "doc", "word", "attachment",
            # common document names people say
            "document", "syllabus", "prescription", "report", "certificate",
            "ticket", "bill", "receipt", "invoice", "resume", "cv",
            "notes", "assignment", "project", "form", "card", "letter",
            "result", "marksheet", "admit", "id", "aadhar", "pan",
            # hindi
            "dastawez", "faili", "parchii", "parchi", "kagaz",
        ]
        for kw in image_retrieve_keywords:
            if kw in msg and any(iw in msg for iw in image_words):
                return "IMAGE_RETRIEVE"

        # ── STEP 3: CHAT — greetings and casual conversation ──
        chat_keywords = [
            "hi", "hello", "hey", "hola", "namaste", "kaise ho", "kya haal",
            "good morning", "good night", "good evening", "thanks", "thank you",
            "dhanyavaad", "shukriya", "bye", "ok", "okay", "haan", "nahi",
            "how are you", "what's up", "sup", "yo", "hii", "hiii",
        ]
        chat_phrases = [
            "acha laga", "bohot badhiya", "sahi hai", "great", "awesome", "nice",
            "accha", "theek hai", "good job", "well done", "perfect", "mast hai",
            "kya baat", "zabardast", "kamaal", "cool", "super", "badiya",
            "tumhe yaad hai", "kya hua", "kaise ho", "all good",
            "happy to", "glad to", "feeling", "matlab", "kyun", "kab se",
        ]

        if msg in chat_keywords or len(msg) < 4:
            return "CHAT"
        for phrase in chat_phrases:
            if phrase in msg:
                return "CHAT"

        # ── STEP 4: TODO — task list intent ──
        todo_keywords = [
            "i have to", "i need to", "i must", "i should",
            "mujhe karna hai", "karna hai", "banana hai", "kharidna hai",
            "mujhe lena hai", "lena hai", "dena hai", "bharna hai",
            "to-do", "todo", "task list", "tasks hai",
        ]
        todo_multi = ["and", "aur", ","]  # multiple items signal
        has_todo_kw = any(kw in msg for kw in todo_keywords)
        has_multi = any(sep in msg for sep in todo_multi)

        # Only TODO if no time indicator (else it becomes REMINDER/CALENDAR)
        time_words = ["baje", "am", "pm", "tomorrow", "kal", "aaj", "today",
                      "monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
        has_time_word = any(tw in msg for tw in time_words)

        if has_todo_kw and not has_time_word:
            return "TODO"

        # ── STEP 5: REMINDER ──
        reminder_keywords = [
            "remind me", "yaad dilana", "reminder set", "alert me",
            "mujhe yaad", "remind karna", "notification bhej",
        ]
        reminder_time_words = ["baje", "am", "pm", "o'clock", "minute", "hour", "ghante"]
        for kw in reminder_keywords:
            if kw in msg:
                return "REMINDER"
        if "remind" in msg and any(tw in msg for tw in reminder_time_words):
            return "REMINDER"

        # ── STEP 5: CALENDAR_EVENT — NEW event being scheduled (NOT a question) ──
        # Only trigger if it's clearly a statement scheduling something new
        calendar_new_indicators = [
            "schedule", "set up", "book", "fix a", "arrange",
            "milna hai", "milenge", "baat karni hai", "rakho",
            "rakh do", "add to calendar", "calendar mein",
        ]
        calendar_event_keywords = [
            "meeting", "meetup", "call", "interview", "appointment",
            "presentation", "demo", "standup", "conference", "seminar",
            "milna", "milenge",
        ]
        time_indicators = [
            "tomorrow", "kal", "aaj", "today",
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "baje", "am", "pm",
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
            "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
        ]
        has_cal_kw = any(kw in msg for kw in calendar_event_keywords)
        has_time = any(tw in msg for tw in time_indicators)

        # Questions are already filtered in STEP 1, so if we reach here
        # and message has event keyword + date/time → it's a new event statement
        if has_cal_kw and has_time:
            return "CALENDAR_EVENT"

        # ── STEP 6: SAVE — explicit save keywords only ──
        import re
        explicit_save_keywords = [
            "save", "note", "remember", "yaad rakh", "likh le", "note kar",
            "save this", "save karo", "save kar", "note down",
        ]
        for kw in explicit_save_keywords:
            if kw in msg:
                return "SAVE"

        # Auto-saveable facts: personal info statements
        saveable_patterns = [
            r'\bpassword\b', r'\bbirthday\b', r'\baddress\b',
            r'\bphone number\b', r'\bflight\b', r'\btrain\b',
            r'\bhotel\b', r'\bdeadline\b', r'\bexam\b', r'\brecipe\b',
            r'mujhe\s+\S+.*pasand',      # "mujhe gulaab jamoon pasand hai"
            r'\bmera\s+\w+\b', r'\bmeri\s+\w+\b',
        ]
        for pattern in saveable_patterns:
            if re.search(pattern, msg):
                return "SAVE"

        # Short messages (< 6 words) → CHAT
        if len(msg.split()) < 6:
            return "CHAT"

        # "pasand hai" / "i like" type statements → SAVE
        info_markers = [
            "hai mera", "hai meri", "hai mere", "mera naam", "meri age",
            "i have", "i am", "i like", "i need", "i want",
            "pasand hai", "chahiye", "wala hai",
        ]
        for marker in info_markers:
            if marker in msg:
                return "SAVE"

        # Default → CHAT (safer than SAVE)
        return "CHAT"

    # ===========================
    # HANDLE TODO / TASK LIST
    # ===========================
    def handle_todo(self, user: BotUser, message: str) -> str:
        """Extract tasks from message, save as memory, reply in numbered format"""
        import json

        prompt = f"""Extract all tasks/to-do items from this message.
Return ONLY a JSON array of short task strings. No extra text.
Example input: "I have to buy milk and vegetables and pay electricity bill"
Example output: ["buy milk", "buy vegetables", "pay electricity bill"]

Message: "{message}"

Tasks:"""

        try:
            response = self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200
            )
            raw = response.choices[0].message.content.strip()

            # Clean if wrapped in code block
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            tasks = json.loads(raw)
            if not isinstance(tasks, list) or not tasks:
                raise ValueError("Empty or invalid task list")

        except Exception as e:
            logger.error(f"Todo extraction error: {e}")
            # Fallback: save as plain memory
            result = self.save_memory(user, message, tags=["todo", "task"])
            return result["message"]

        # Save all tasks as one memory
        task_text = "\n".join([f"{i+1}. {t.capitalize()}" for i, t in enumerate(tasks)])
        full_content = f"[Todo List]\n{task_text}"
        self.save_memory(user, full_content, tags=["todo", "task"])

        # Build reply
        lines = ["✅ *Tasks saved!*\n"]
        for i, task in enumerate(tasks, 1):
            lines.append(f"{i}. {task.capitalize()}")
        lines.append("\n_Type /ask my tasks to see them later._")

        return "\n".join(lines)

    # ===========================
    # REMINDER ACKNOWLEDGMENT
    # ===========================
    ACK_KEYWORDS = {
        'got it', 'gotit', 'done', 'ok done', 'seen', 'received',
        'dekh liya', 'dekha', 'mil gaya', 'mil gayi', 'theek hai',
        'haan', 'haa', 'ok', 'okay', 'noted', 'acknowledged',
        'dismiss', 'stop reminder', 'reminder dismiss',
    }

    def _is_reminder_ack(self, message: str) -> bool:
        """Return True if message is a reminder acknowledgment"""
        msg = message.lower().strip().rstrip('!.')
        return msg in self.ACK_KEYWORDS

    def acknowledge_reminder(self, user: BotUser) -> str:
        """Mark the most recent unacknowledged+sent reminder as acknowledged"""
        from memory_engine.models import Reminder
        reminder = Reminder.objects.filter(
            user=user, is_sent=True, is_acknowledged=False
        ).order_by('-last_sent_at').first()

        if reminder:
            reminder.is_acknowledged = True
            reminder.save(update_fields=['is_acknowledged'])
            return (
                f"✅ Got it! *'{reminder.content}'* reminder dismissed.\n\n"
                f"You won't be reminded again. 🌟"
            )
        return "✅ No active reminders to dismiss right now!"

    # ===========================
    # GET RECENT MEMORIES
    # ===========================
    def get_recent_memories(self, user: BotUser, limit: int = 5) -> str:
        """Get user's most recent memories"""
        memories = Memory.objects.filter(
            user=user,
            is_deleted=False
        ).order_by('-created_at')[:limit]

        if not memories:
            return "📭 No memories saved yet!\n\nStart saving with:\n/save Your first memory here"

        lines = ["📋 *Your Recent Memories:*\n"]
        for i, mem in enumerate(memories, 1):
            date = mem.created_at.strftime("%d %b, %I:%M %p")
            tags = " ".join([f"#{t}" for t in mem.tags[:3]]) if mem.tags else ""
            lines.append(f"{i}. {mem.content_preview[:80]}{'...' if len(mem.content_preview) > 80 else ''}")
            lines.append(f"   📅 {date} {tags}\n")

        lines.append(f"\n📊 Total: {user.memory_count} memories")
        return "\n".join(lines)

    # ===========================
    # DELETE MEMORY
    # ===========================
    def delete_memory(self, user: BotUser, memory_index: int) -> str:
        """Delete a memory by its index (from /list)"""
        memories = Memory.objects.filter(
            user=user,
            is_deleted=False
        ).order_by('-created_at')

        if memory_index < 1 or memory_index > memories.count():
            return "❌ Invalid memory number. Use /list to see your memories."

        memory = memories[memory_index - 1]

        # Delete from ChromaDB
        try:
            self.vectorstore._collection.delete(ids=[memory.chroma_id])
        except Exception as e:
            logger.error(f"ChromaDB delete error: {e}")

        # Soft delete in DB
        memory.is_deleted = True
        memory.save()

        user.memory_count = max(0, user.memory_count - 1)
        user.save()

        return f"🗑️ Deleted: \"{memory.content_preview[:60]}...\""

    # ===========================
    # AUTO-TAGGING
    # ===========================
    def _auto_tag(self, content: str) -> list:
        """Auto-generate tags for a memory using LLM"""
        try:
            tag_prompt = f"""Generate 2-3 short tags for this memory. 
Reply with ONLY comma-separated tags, nothing else.
Examples: meeting, work, deadline | recipe, cooking, dinner | idea, project, startup

Memory: "{content[:200]}"

Tags:"""
            response = self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": tag_prompt}],
                temperature=0.5,
                max_tokens=50
            )
            tags = [t.strip().lower().replace("#", "") for t in response.choices[0].message.content.split(",")]
            return tags[:3]
        except Exception:
            return ["general"]

    # ===========================
    # SEARCH BY TAG
    # ===========================
    def search_by_tag(self, user: BotUser, tag: str) -> str:
        """Search memories by tag"""
        memories = Memory.objects.filter(
            user=user,
            is_deleted=False,
            tags__contains=[tag.lower()]
        ).order_by('-created_at')[:10]

        if not memories:
            return f"🔍 No memories found with tag #{tag}"

        lines = [f"🏷️ *Memories tagged #{tag}:*\n"]
        for i, mem in enumerate(memories, 1):
            lines.append(f"{i}. {mem.content_preview[:80]}")

        return "\n".join(lines)

    # ===========================
    # MEMORY STATS
    # ===========================
    def get_stats(self, user: BotUser) -> str:
        """Get user's memory statistics"""
        total = user.memory_count
        tier = "🆓 Free" if user.tier == "free" else "⭐ Pro"
        limit = 50 if user.tier == "free" else 10000

        # Count by source
        by_source = Memory.objects.filter(
            user=user, is_deleted=False
        ).values('source').distinct().count()

        # Recent activity
        recent = Memory.objects.filter(
            user=user, is_deleted=False
        ).order_by('-created_at').first()

        last_saved = recent.created_at.strftime("%d %b %Y") if recent else "Never"

        return f"""📊 *Your MemoRax Stats*

🧠 Total Memories: {total}/{limit}
📱 Plan: {tier}
💬 Messages Today: {user.messages_today}
📅 Last Saved: {last_saved}
📂 Sources: {by_source}

{'⚠️ Nearing limit! Type /upgrade for Pro' if total > limit * 0.8 else '✨ Keep saving memories!'}"""

    # ===========================
    # GENERAL CHAT
    # ===========================
    def _general_chat(self, user: BotUser, message: str) -> str:
        """Handle general conversation (not save/query) with conversation history"""
        recent_history = ConversationHistory.objects.filter(
            user=user
        ).order_by('-created_at')[:6]

        history_text = ""
        if recent_history:
            history_items = reversed(list(recent_history))
            history_text = "\n".join(
                [f"{'User' if h.role == 'user' else 'MemoRax'}: {h.content}" for h in history_items]
            )

        system_prompt = f"""You are MemoRax AI, a friendly personal memory assistant on WhatsApp.
The user is having a casual conversation. Respond naturally and warmly.

Rules:
- Reply in the SAME LANGUAGE the user writes in (Hindi, English, or Hinglish)
- If user writes in Hindi/Devanagari, reply in Hindi
- If user writes in English, reply in English
- If user mixes (Hinglish), you can mix too
- Be concise (2-3 sentences max, this is WhatsApp)
- You can mention that you're a memory assistant if relevant
- Do NOT make up memories or facts about the user

User's name: {user.name or 'Friend'}

Recent Conversation:
{history_text}"""

        try:
            response = self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                temperature=0.8,
                max_tokens=300
            )
            answer = response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq chat error: {e}")
            answer = "Hey! I'm here to help you remember things. Try saving a memory or ask me something!"

        ConversationHistory.objects.create(user=user, role="user", content=message)
        ConversationHistory.objects.create(user=user, role="assistant", content=answer)
        return answer

    # ===========================
    # IMAGE ANALYSIS (Gemini Vision)
    # ===========================
    def analyze_image(self, user: BotUser, image_url: str, media_type: str, caption: str = "") -> dict:
        """Download image from Twilio, analyze with Groq Vision (Llama), save as memory, and return image data"""
        # Download image from Twilio (requires auth)
        try:
            response = requests.get(
                image_url,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                timeout=30
            )
            response.raise_for_status()
            image_bytes = response.content
        except Exception as e:
            logger.error(f"Image download error: {e}")
            return {
                "success": False,
                "message": "Sorry, I couldn't download the image. Please try again."
            }

        # Save image locally for later retrieval
        import os
        media_dir = settings.MEDIA_ROOT / 'images' / user.phone.replace(':', '_')
        os.makedirs(media_dir, exist_ok=True)

        # Generate unique filename
        file_ext = media_type.split('/')[-1].lower()
        if file_ext == 'jpeg':
            file_ext = 'jpg'
        filename = f"img_{uuid.uuid4().hex[:12]}.{file_ext}"
        local_path = media_dir / filename

        # Save image to disk
        with open(local_path, 'wb') as f:
            f.write(image_bytes)

        # Store relative path (from MEDIA_ROOT)
        relative_path = f"images/{user.phone.replace(':', '_')}/{filename}"

        # Analyze with Groq Vision (Llama 4 Scout Vision - 2026)
        try:
            import base64

            # Convert image to base64
            base64_image = base64.b64encode(image_bytes).decode('utf-8')

            # Detect image format (JPEG, PNG, etc.)
            image_format = media_type.split('/')[-1].upper()
            if image_format == 'JPEG':
                image_format = 'JPEG'
            elif image_format == 'PNG':
                image_format = 'PNG'
            else:
                image_format = 'JPEG'  # default fallback

            # Build detailed prompt for image analysis
            user_context = caption if caption else 'No caption provided'
            prompt = f"""Analyze this image in EXTREME DETAIL for accurate retrieval later. Include:

1. **Main Objects/Subjects**: Identify ALL visible objects, devices, phones, items. If it's a phone, identify the EXACT MODEL (OnePlus, Poco, iPhone, Samsung, etc.) and distinctive features (camera design, shape, color).
2. **Brand/Model Names**: If any branded product is visible, state the exact brand name and model number.
3. **Colors & Materials**: Specific colors, metallic/plastic/glass finishes.
4. **Distinctive Features**: Unique design elements, camera layout (single/dual/triple lens), button placement, notch/punch-hole.
5. **Text Visible**: Any readable text, labels, model numbers, serial numbers.
6. **Setting/Context**: Where is this? indoors/outdoors, room type, background items.
7. **Notable Details**: Anything that makes this photo UNIQUE and searchable - scratches, dents, stickers, accessories.

User Note: {user_context}

Format as a detailed searchable description (5-7 sentences). Focus on SPECIFIC, IDENTIFIABLE details that will help find this exact image when user asks."""

            # Call Groq Vision API (Llama 4 Scout - latest multimodal model)
            vision_response = self.groq.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/{image_format.lower()};base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                temperature=0.7,
                max_tokens=500
            )
            description = vision_response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq Vision error: {e}")
            # Fallback: save with caption only
            if caption:
                result = self.save_memory(user, f"[Image] {caption}", source="image", media_path=relative_path)
                return {
                    "success": True,
                    "media_path": relative_path,
                    "description": f"📸 {caption}\n\n_AI analysis unavailable, but image saved._"
                }
            return {
                "success": False,
                "message": "Sorry, I couldn't analyze the image. Try adding a caption to describe it."
            }

        # Save as memory with local image path
        memory_content = f"[Image: {caption}] {description}" if caption else f"[Image] {description}"
        result = self.save_memory(user, memory_content, source="image", media_path=relative_path)

        # Also save to SavedFile vault
        SavedFile.objects.create(
            user=user,
            name=filename,
            file_type='image',
            file_path=relative_path,
            caption=caption,
            ai_description=description,
        )

        if result["success"]:
            return {
                "success": True,
                "media_path": relative_path,
                "description": f"📸 *Image saved!*\n\n{description}\n\n_Say \"send my image\" anytime to get it back._"
            }
        return {
            "success": False,
            "message": "❌ Image downloaded but couldn't save to memory. Please try again."
        }

    # ===========================
    # VOICE TRANSCRIPTION (Groq Whisper)
    # ===========================
    def transcribe_voice(self, user: BotUser, audio_url: str, caption: str = ""):
        """Download voice note from Twilio, transcribe with Groq Whisper, save as memory"""
        # Download audio from Twilio (requires auth)
        try:
            response = requests.get(
                audio_url,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                timeout=30
            )
            response.raise_for_status()
            audio_bytes = response.content
        except Exception as e:
            logger.error(f"Audio download error: {e}")
            return {
                "success": False,
                "message": "Sorry, I couldn't download the voice note. Please try again."
            }

        # Transcribe with Groq Whisper
        try:
            import tempfile
            import os

            # Groq requires a file object, so write to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_audio:
                temp_audio.write(audio_bytes)
                temp_audio_path = temp_audio.name

            with open(temp_audio_path, "rb") as audio_file:
                transcription = self.groq.audio.transcriptions.create(
                    model="whisper-large-v3",
                    file=audio_file,
                    language="en"  # auto-detects Hindi/English
                )

            os.unlink(temp_audio_path)  # Delete temp file
            transcribed_text = transcription.text

        except Exception as e:
            logger.error(f"Groq Whisper error: {e}")
            return {
                "success": False,
                "message": "Sorry, I couldn't transcribe the voice note. Please try again."
            }

        # Process transcribed text through smart_chat for intent detection
        response = self.smart_chat(user, transcribed_text)
        
        # If response is a dict (e.g., image retrieval), return it with voice prefix
        if isinstance(response, dict):
            if response.get("success") and response.get("media_path"):
                # Update description to include voice context
                updated_desc = f"🎤 Voice: \"{transcribed_text}\"\n\n{response.get('description', '')}"
                response["description"] = updated_desc
            return response
        
        # Otherwise, it's a string response
        return f"🎤 Voice note: \"{transcribed_text}\"\n\n{response}"

    # ===========================
    # REMINDERS
    # ===========================
    def parse_and_create_reminder(self, user: BotUser, message: str) -> str:
        """Parse a reminder request and create a scheduled reminder"""
        import re
        import pytz

        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)

        # --- Step 1: Extract time (regex, no LLM) ---
        hour, minute = None, 0
        msg = message.lower()

        # Match patterns like "10 pm", "10pm", "3:30 pm", "5 am", "10:00pm"
        time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)', msg)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            period = time_match.group(3)
            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0

        # Match "5 baje", "3 baje", "8 baje" etc.
        if hour is None:
            baje_match = re.search(r'(\d{1,2})\s*baje', msg)
            if baje_match:
                hour = int(baje_match.group(1))
                # "baje" without AM/PM context: treat <= 8 as PM (1-8 baje = 1PM-8PM)
                # 9+ baje could be morning (9am, 10am) - leave as-is
                if 1 <= hour <= 8:
                    hour += 12

        # Match 24-hour format like "22:00", "17:30"
        if hour is None:
            h24_match = re.search(r'(\d{1,2}):(\d{2})', msg)
            if h24_match:
                hour = int(h24_match.group(1))
                minute = int(h24_match.group(2))

        # Validate hour is in valid range
        if hour is not None and (hour < 0 or hour > 23):
            hour = None
        if minute is not None and (minute < 0 or minute > 59):
            minute = 0

        if hour is None:
            # Default: 1 hour from now
            remind_dt = now + timedelta(hours=1)
        else:
            remind_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # --- Step 2: Extract date ---
        date_set = False
        if "tomorrow" in msg or "kal" in msg:
            remind_dt = remind_dt + timedelta(days=1)
            remind_dt = remind_dt.replace(hour=hour or remind_dt.hour, minute=minute)
            date_set = True

        # Match "17 feb", "18 march", "25 jan" etc.
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
            "january": 1, "february": 2, "march": 3, "april": 4,
            "june": 6, "july": 7, "august": 8, "september": 9,
            "october": 10, "november": 11, "december": 12,
        }
        if not date_set:
            date_match = re.search(r'(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)', msg)
            if date_match:
                day = int(date_match.group(1))
                month = months[date_match.group(2)]
                year = now.year
                if month < now.month or (month == now.month and day < now.day):
                    year += 1
                remind_dt = remind_dt.replace(year=year, month=month, day=day)
                date_set = True

        # If time already passed today, move to tomorrow
        if not date_set and remind_dt <= now:
            remind_dt += timedelta(days=1)

        # --- Step 3: Extract content (remove time/date words, keep the rest) ---
        content = message
        # Remove common prefixes
        for prefix in ["remind me to ", "remind me ", "mujhe yaad dilana ", "yaad dilana ", "reminder "]:
            if content.lower().startswith(prefix):
                content = content[len(prefix):]
                break
        # Remove time patterns from content
        content = re.sub(r'\b\d{1,2}(:\d{2})?\s*(am|pm|baje)\b', '', content, flags=re.IGNORECASE)
        # Remove date patterns
        content = re.sub(r'\b\d{1,2}\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b', '', content, flags=re.IGNORECASE)
        content = re.sub(r'\b(tomorrow|kal|today|aaj|at)\b', '', content, flags=re.IGNORECASE)
        content = content.strip().strip(",.!-").strip()

        if not content:
            content = message  # fallback to original message

        # --- Step 4: Create reminder ---
        try:
            Reminder.objects.create(
                user=user,
                content=content,
                remind_at=remind_dt
            )
            formatted_time = remind_dt.strftime('%d %b, %I:%M %p')
            return f"⏰ Reminder set!\n\n📝 {content}\n🕐 {formatted_time}"
        except Exception as e:
            logger.error(f"Reminder save error: {e}")
            return "Sorry, I couldn't set that reminder. Please try again."

    # ===========================
    # FILE / IMAGE RETRIEVAL
    # ===========================
    def _detect_document_type(self, text: str) -> str:
        """Detect document type from content"""
        text_lower = text.lower()
        
        # Detect common document types
        if any(word in text_lower for word in ['invoice', 'bill', 'amount', 'total due']):
            return "Invoice/Bill"
        elif any(word in text_lower for word in ['receipt', 'transaction', 'payment']):
            return "Receipt"
        elif any(word in text_lower for word in ['student', 'grade', 'marks', 'score', 'class']):
            return "Academic Document"
        elif any(word in text_lower for word in ['certificate', 'certified', 'awarded']):
            return "Certificate"
        elif any(word in text_lower for word in ['prescription', 'medicine', 'doctor', 'hospital']):
            return "Medical Document"
        elif any(word in text_lower for word in ['contract', 'agreement', 'terms', 'conditions']):
            return "Legal Document"
        elif any(word in text_lower for word in ['syllabus', 'course', 'lecture', 'chapter']):
            return "Educational Material"
        elif any(word in text_lower for word in ['resume', 'cv', 'experience', 'qualification']):
            return "Resume/CV"
        else:
            return "Document"

    def retrieve_image(self, user: BotUser, query: str) -> dict:
        """Find and return saved files (images, PDFs) matching the query with ENHANCED search"""
        import os
        import re

        q = query.lower().strip()
        vault_files = list(SavedFile.objects.filter(user=user))
        
        if not vault_files:
            return {
                "success": False,
                "message": "📭 No matching files found.\n\nSend me an image or PDF first — I'll save it to your vault!",
            }

        # ══════════════════════════════════════════════════════════════
        # STAGE 1: KEYWORD MATCHING (High Priority)
        # ══════════════════════════════════════════════════════════════
        scored_files = []
        
        # Extract important keywords from query
        # Check for brand names first (OnePlus, Poco, iPhone, Samsung, etc.)
        brand_names = ["oneplus", "1+", "poco", "iphone", "samsung", "nokia", "motorola", 
                       "realme", "oppo", "vivo", "sony", "lg", "google pixel"]
        query_brands = [b for b in brand_names if b in q]
        
        # Model/feature keywords
        feature_keywords = ["camera", "screen", "display", "battery", "processor", "color", 
                          "design", "notch", "lens", "metallic", "glass", "plastic"]
        query_features = [f for f in feature_keywords if f in q]
        
        for f in vault_files:
            haystack = f" {f.name} {f.caption} {f.ai_description} ".lower()
            score = 0
            
            # 1. Brand name matching (high weight)
            for brand in query_brands:
                if brand in haystack:
                    score += 50  # High priority
            
            # 2. Feature/detail matching (medium weight)
            for feature in query_features:
                if feature in haystack:
                    score += 20
            
            # 3. General word matching (low weight)
            words = [w for w in q.split() if len(w) > 2 and w not in query_brands and w not in query_features]
            for word in words:
                if word in haystack:
                    score += 5
                # Also check for word substrings (e.g., "phone" matches "smartphone")
                if word in haystack or haystack.count(word) > 0:
                    score += 2
            
            if score > 0:
                scored_files.append((f, score))
        
        # Sort by score
        scored_files.sort(key=lambda x: x[1], reverse=True)
        
        if scored_files and scored_files[0][1] > 0:
            best_file = scored_files[0][0]
            return {
                "success": True,
                "media_path": best_file.file_path,
                "description": best_file.caption or best_file.ai_description or best_file.name,
            }

        # ══════════════════════════════════════════════════════════════
        # STAGE 2: SEMANTIC SEARCH (ChromaDB)
        # ══════════════════════════════════════════════════════════════
        try:
            # Search with larger k value to get more results
            results = self.vectorstore.similarity_search(
                query,
                k=10,  # Get more results for better matching
                filter={"user_phone": user.phone}
            )
            
            # Return first match with media path
            for res in results:
                src = res.metadata.get("source", "")
                if src in ("image", "document"):
                    chroma_id = res.metadata.get("chroma_id")
                    try:
                        memory = Memory.objects.get(chroma_id=chroma_id, user=user)
                        if memory.media_path:
                            return {
                                "success": True,
                                "media_path": memory.media_path,
                                "description": memory.content_preview,
                            }
                    except Memory.DoesNotExist:
                        continue
        except Exception as e:
            logger.error(f"Semantic search error: {e}")

        # ══════════════════════════════════════════════════════════════
        # STAGE 3: FALLBACK (Most Recent File)
        # ══════════════════════════════════════════════════════════════
        # Get most recent as last resort
        recent = SavedFile.objects.filter(user=user).order_by('-created_at').first()
        if recent:
            return {
                "success": True,
                "media_path": recent.file_path,
                "description": f"📁 Latest: {recent.caption or recent.name}",
            }

        return {
            "success": False,
            "message": "📭 No matching files found.\n\nSend me an image or PDF first — I'll save it to your vault!",
        }

    # ===========================
    # DOCUMENT / PDF SAVE
    # ===========================
    def save_document(self, user: BotUser, doc_url: str, media_type: str, caption: str = "") -> dict:
        """Download PDF/document from Twilio and save to vault + memory, return document data"""
        import os

        # Download from Twilio
        try:
            response = requests.get(
                doc_url,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                timeout=30
            )
            response.raise_for_status()
            doc_bytes = response.content
        except Exception as e:
            logger.error(f"Document download error: {e}")
            return {
                "success": False,
                "message": "❌ Couldn't download the document. Please try again."
            }

        # Determine extension
        ext = 'pdf' if 'pdf' in media_type else media_type.split('/')[-1].lower() or 'bin'
        filename = caption.replace(' ', '_')[:40] if caption else f"doc_{uuid.uuid4().hex[:8]}"
        filename = f"{filename}.{ext}"

        # Save to disk
        doc_dir = settings.MEDIA_ROOT / 'documents' / user.phone.replace(':', '_')
        os.makedirs(doc_dir, exist_ok=True)
        local_path = doc_dir / filename
        with open(local_path, 'wb') as f:
            f.write(doc_bytes)

        relative_path = f"documents/{user.phone.replace(':', '_')}/{filename}"

        # Try to extract text from PDF for memory search
        text_preview = caption or filename
        document_summary = ""
        try:
            import io
            import PyPDF2
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(doc_bytes))
            
            # Extract text from first 5 pages (more comprehensive)
            extracted_pages = []
            for i, page in enumerate(pdf_reader.pages[:5]):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    extracted_pages.append(page_text)
            
            extracted = " ".join(extracted_pages).strip()
            
            if extracted:
                # Use full extracted text for semantic search (up to 2000 chars)
                text_preview = extracted[:2000]
                
                # Create a summary focusing on key info
                document_summary = f"""PDF Document: {filename}
{f'Caption: {caption}' if caption else ''}
Document Type: {self._detect_document_type(extracted)}
Key Content: {extracted[:500]}"""
        except Exception as e:
            logger.error(f"PDF extraction error: {e}")
            pass  # No PyPDF2 or unreadable — that's fine

        # Save memory for semantic search
        memory_content = document_summary if document_summary else f"[Document: {caption or filename}] {text_preview}"
        self.save_memory(user, memory_content, source="document", media_path=relative_path)

        # Save to vault
        SavedFile.objects.create(
            user=user,
            name=filename,
            file_type='pdf' if ext == 'pdf' else 'other',
            file_path=relative_path,
            caption=caption,
            ai_description=text_preview[:300],
        )

        description = f"📄 *{filename}*\n{('Caption: ' + caption + chr(10)) if caption else ''}✅ Saved to your vault!"
        return {
            "success": True,
            "media_path": relative_path,
            "description": description
        }

    # ===========================
    # CALENDAR EVENT CREATION
    # ===========================
    def create_calendar_event(self, user: BotUser, message: str) -> str:
        """Use Groq LLM to extract event details and save to CalendarEvent"""
        import json
        import pytz

        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)

        system_prompt = f"""You are a calendar assistant. Extract event details from the user's message.
Today's date and time is: {now.strftime('%A, %d %B %Y %I:%M %p IST')}

Return ONLY a valid JSON object with these exact keys:
{{
  "title": "short event title (max 60 chars)",
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM",
  "end_time": "HH:MM",
  "participants": ["name1", "name2"],
  "location": "place or empty string",
  "description": "any extra details or empty string",
  "color": "blue"
}}

Rules:
- color choices: blue (meetings), green (personal), red (deadline), purple (appointment), orange (other)
- If no end time given, assume 1 hour duration
- If no date given but time mentioned, assume today or tomorrow (whichever is future)
- participants: extract only person names, not the user themselves
- Return ONLY the JSON, no other text"""

        try:
            response = self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                temperature=0.1,
                max_tokens=400
            )
            raw = response.choices[0].message.content.strip()

            # Clean JSON if wrapped in code blocks
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            data = json.loads(raw)
        except Exception as e:
            logger.error(f"Calendar event extraction error: {e}")
            # Fallback: save as memory instead
            result = self.save_memory(user, message)
            return result["message"] + "\n\n_(Tip: Mention a date/time clearly to add to calendar)_"

        # Build datetime objects
        try:
            date_str = data.get("date", now.strftime("%Y-%m-%d"))
            start_str = data.get("start_time", "10:00")
            end_str = data.get("end_time", "11:00")

            start_dt = ist.localize(datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M"))
            end_dt = ist.localize(datetime.strptime(f"{date_str} {end_str}", "%Y-%m-%d %H:%M"))

            # If end is before start (e.g. 10pm–11pm crossing midnight edge case)
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=1)

        except Exception as e:
            logger.error(f"Calendar datetime parse error: {e}")
            return "⚠️ Couldn't parse date/time. Please mention clearly like: 'Meeting with Rahul on 25 Feb at 3pm'"

        # Save to DB
        try:
            event = CalendarEvent.objects.create(
                user=user,
                title=data.get("title", message[:60]),
                description=data.get("description", ""),
                start_time=start_dt,
                end_time=end_dt,
                location=data.get("location", ""),
                participants=data.get("participants", []),
                color=data.get("color", "blue"),
                source="whatsapp",
            )
        except Exception as e:
            logger.error(f"Calendar save error: {e}")
            return "Sorry, couldn't save the event. Please try again."

        # Also save to ChromaDB so query_memory can find it via semantic search
        try:
            parts = [f"participants: {', '.join(event.participants)}"] if event.participants else []
            if event.location:
                parts.append(f"location: {event.location}")
            if event.description:
                parts.append(event.description)
            chroma_text = (
                f"[Calendar Event] {event.title} on "
                f"{start_dt.strftime('%d %B %Y')} "
                f"({start_dt.strftime('%A')}) "
                f"from {start_dt.strftime('%I:%M %p')} to {end_dt.strftime('%I:%M %p')}. "
                + " ".join(parts)
            )
            self.save_memory(user, chroma_text, tags=["calendar", "event"])
        except Exception as e:
            logger.error(f"Calendar ChromaDB sync error: {e}")

        # Auto-set a reminder 30 minutes before
        reminder_time = start_dt - timedelta(minutes=30)
        if reminder_time > tz.now():
            try:
                Reminder.objects.create(
                    user=user,
                    content=f"Upcoming: {event.title}",
                    remind_at=reminder_time
                )
                reminder_note = f"⏰ Reminder set for {reminder_time.strftime('%I:%M %p')}"
            except Exception:
                reminder_note = ""
        else:
            reminder_note = ""

        # Build reply
        participants_line = f"\n👥 {', '.join(event.participants)}" if event.participants else ""
        location_line = f"\n📍 {event.location}" if event.location else ""
        desc_line = f"\n📝 {event.description}" if event.description else ""

        return (
            f"📅 *Event Added to Calendar!*\n\n"
            f"🗓️ {event.title}\n"
            f"📆 {start_dt.strftime('%d %b %Y')}\n"
            f"🕐 {start_dt.strftime('%I:%M %p')} – {end_dt.strftime('%I:%M %p')}"
            f"{location_line}{participants_line}{desc_line}\n"
            f"{reminder_note}\n\n"
            f"View in dashboard: /dashboard"
        )
