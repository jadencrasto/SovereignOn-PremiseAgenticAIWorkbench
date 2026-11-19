"""Stdin/stdout worker for the local-development importer."""
import sys
from pathlib import Path

if __name__ == '__main__':
    def restrict_io(event, _args):
        if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.startfile', 'os.exec', 'os.spawn'}:
            raise RuntimeError('External execution is unavailable in the local parser.')

    sys.addaudithook(restrict_io)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.multimodal.document_evidence import ExtractionLimit
    from backend.rag.local_documents import MAX_DOCUMENT_BYTES, NoDocumentEvidence, project_document

    try:
        data = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise ExtractionLimit
        result = project_document(sys.argv[1], data)
        sys.stdout.buffer.write(result.model_dump_json(exclude_none=True).encode('utf-8'))
    except NoDocumentEvidence:
        raise SystemExit(3) from None
    except ExtractionLimit:
        raise SystemExit(4) from None
    except ImportError:
        raise SystemExit(5) from None
    except Exception:
        raise SystemExit(2) from None
