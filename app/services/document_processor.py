import os
import logging

logger = logging.getLogger(__name__)

def extract_text_and_elements(file_path: str, file_type: str) -> list[dict]:
    """
    Extract text elements from document.
    Returns a list of dicts: [{"type": "heading"|"paragraph", "text": str}]
    """
    file_type = file_type.upper()
    elements = []
    
    if file_type == "PDF":
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.error("pymupdf (fitz) is not installed. PDF extraction is unavailable.")
            raise ImportError("pymupdf is required for PDF extraction but not installed.")
            
        doc = fitz.open(file_path)
        for page in doc:
            blocks = page.get_text("blocks")
            for block in blocks:
                text = block[4].strip()
                if not text:
                    continue
                lines = text.split("\n")
                if len(lines) == 1 and len(text) < 120 and (
                    text.isupper() or 
                    any(text.lower().startswith(prefix) for prefix in ["chapter", "section", "module", "unit", "part"])
                ):
                    elements.append({"type": "heading", "text": text})
                else:
                    elements.append({"type": "paragraph", "text": text.replace("\n", " ")})
                    
    elif file_type == "DOCX":
        try:
            import docx
        except ImportError:
            logger.error("python-docx is not installed. DOCX extraction is unavailable.")
            raise ImportError("python-docx is required for DOCX extraction but not installed.")
            
        doc = docx.Document(file_path)
        for p in doc.paragraphs:
            text = p.text.strip()
            if not text:
                continue
            is_heading = p.style.name.startswith("Heading") or (len(text) < 120 and text.isupper())
            elements.append({
                "type": "heading" if is_heading else "paragraph",
                "text": text
            })
            
    elif file_type == "PPTX":
        try:
            from pptx import Presentation
        except ImportError:
            logger.error("python-pptx is not installed. PPTX extraction is unavailable.")
            raise ImportError("python-pptx is required for PPTX extraction but not installed.")
            
        prs = Presentation(file_path)
        for slide in prs.slides:
            title = ""
            if slide.shapes.title:
                title = slide.shapes.title.text.strip()
                if title:
                    elements.append({"type": "heading", "text": title})
            for shape in slide.shapes:
                if shape.has_text_frame and shape != slide.shapes.title:
                    for paragraph in shape.text_frame.paragraphs:
                        text = paragraph.text.strip()
                        if text and text != title:
                            elements.append({"type": "paragraph", "text": text})
                            
    elif file_type in ("TXT", "TEXT"):
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        paragraphs = text.split("\n\n")
        for p in paragraphs:
            p_text = p.strip()
            if not p_text:
                continue
            is_heading = p_text.startswith("#") or (len(p_text) < 100 and p_text.isupper())
            elements.append({
                "type": "heading" if is_heading else "paragraph",
                "text": p_text.lstrip("# ").strip()
            })
            
    return elements


def chunk_document(elements: list[dict], max_tokens: int = 800, overlap_tokens: int = 100) -> list[str]:
    """
    Chunk document elements using a heading-aware token-based splitter.
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        def count_tokens(text: str) -> int:
            return len(enc.encode(text))
        def decode_tokens(toks: list[int]) -> str:
            return enc.decode(toks)
        def encode_tokens(text: str) -> list[int]:
            return enc.encode(text)
    except Exception:
        # Fallback to character-based heuristic (~4 characters per token)
        logger.warning("tiktoken not available, using fallback character counting")
        def count_tokens(text: str) -> int:
            return len(text) // 4
        def decode_tokens(toks: list[int]) -> str:
            return "".join(chr(t) for t in toks)
        def encode_tokens(text: str) -> list[int]:
            return [ord(c) for c in text]
        
    chunks = []
    current_chunk_texts = []
    current_tokens = 0
    
    for el in elements:
        el_text = el["text"].strip()
        if not el_text:
            continue
        el_tokens = count_tokens(el_text)
        
        # Heading-aware splitting: close previous chunk if it has enough content (>= 150 tokens)
        if el["type"] == "heading" and current_tokens >= 150:
            chunks.append("\n\n".join(current_chunk_texts))
            current_chunk_texts = []
            current_tokens = 0
            
        # Append or split
        if current_tokens + el_tokens <= max_tokens:
            current_chunk_texts.append(el_text)
            current_tokens += el_tokens
        else:
            if current_chunk_texts:
                chunks.append("\n\n".join(current_chunk_texts))
                # Add overlap to the next chunk from the end of the current chunk
                overlap_texts = []
                overlap_cnt = 0
                for text in reversed(current_chunk_texts):
                    t_tok = count_tokens(text)
                    if overlap_cnt + t_tok <= overlap_tokens:
                        overlap_texts.insert(0, text)
                        overlap_cnt += t_tok
                    else:
                        break
                current_chunk_texts = overlap_texts
                current_tokens = overlap_cnt
                
            # If the single element is larger than max_tokens, split it with overlap
            if el_tokens > max_tokens:
                tokens = encode_tokens(el_text)
                idx = 0
                while idx < len(tokens):
                    chunk_toks = tokens[idx : idx + max_tokens]
                    chunks.append(decode_tokens(chunk_toks))
                    idx += (max_tokens - overlap_tokens)
                current_chunk_texts = []
                current_tokens = 0
            else:
                current_chunk_texts.append(el_text)
                current_tokens += el_tokens
                
    if current_chunk_texts:
        chunks.append("\n\n".join(current_chunk_texts))
        
    return [c.strip() for c in chunks if c.strip()]
