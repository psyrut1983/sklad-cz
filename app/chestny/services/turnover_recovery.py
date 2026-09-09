"""Bounded, read-only CRPT reconciliation of durable pending documents."""
import threading


def reconcile_pending(app):
    from app.chestny.factory import db
    from app.chestny.models import TurnoverDocument
    from app.chestny.turnover_routes import reconcile_document
    with app.app_context():
        ids = [doc.id for doc in TurnoverDocument.query.filter(
            TurnoverDocument.state.in_(['SENT', 'VERIFYING']),
            TurnoverDocument.document_id.isnot(None)).order_by(TurnoverDocument.created_at).limit(20)]
        for doc_id in ids:
            try:
                reconcile_document(doc_id)
            except Exception:
                db.session.rollback()
                app.logger.warning('Automatic turnover reconciliation deferred')
            finally:
                db.session.remove()


def start_reconciler(app):
    stop = threading.Event()
    def run():
        while not stop.wait(60):
            reconcile_pending(app)
    thread = threading.Thread(target=run, name='turnover-reconciliation', daemon=True)
    thread.start()
    app.extensions['turnover_reconciler_stop'] = stop
