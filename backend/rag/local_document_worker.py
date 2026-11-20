"""Stdin/stdout worker for the local-development importer."""
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

if __name__ == '__main__':
    def restrict_io(event, _args):
        if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.startfile', 'os.exec', 'os.spawn'}:
            raise RuntimeError('External execution is unavailable in the local parser.')

    sys.addaudithook(restrict_io)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.multimodal.document_evidence import ExtractionLimit
    from backend.rag.local_documents import MAX_DOCUMENT_BYTES, NoDocumentEvidence, project_document
    from backend.multimodal.image_processor import ImageValidationError
    from backend.multimodal.local_images import MAX_IMAGE_BYTES, normalize_image

    try:
        kind = sys.argv[1]
        if kind in ('artifact-docx', 'artifact-xlsx', 'artifact-pdf'):
            from backend.tools.local_draft_export import MAX_EXPORT_INPUT, generate_in_worker

            data = sys.stdin.buffer.read(MAX_EXPORT_INPUT + 1)
            if len(data) > MAX_EXPORT_INPUT:
                raise ExtractionLimit
            # Keep renderer diagnostics out of the binary response stream.
            with redirect_stdout(sys.stderr):
                output = generate_in_worker(kind[9:], data)
            sys.stdout.buffer.write(output)
            raise SystemExit(0)
        image = kind in ('image-png', 'image-jpeg', 'image-webp')
        limit = MAX_IMAGE_BYTES if image else MAX_DOCUMENT_BYTES
        data = sys.stdin.buffer.read(limit + 1)
        if len(data) > limit:
            raise ExtractionLimit
        if image:
            output = json.dumps(normalize_image(kind[6:], data))
        else:
            output = project_document(kind, data).model_dump_json(exclude_none=True)
        sys.stdout.buffer.write(output.encode('utf-8'))
    except ImageValidationError as exc:
        raise SystemExit(4 if exc.code in ('excessive_dimensions', 'oversized') else 2) from None
    except NoDocumentEvidence:
        raise SystemExit(3) from None
    except ExtractionLimit:
        raise SystemExit(4) from None
    except ImportError:
        raise SystemExit(5) from None
    except Exception:
        raise SystemExit(2) from None
