from app import create_app
app = create_app()
app.recover_pending()   # after a restart, re-queue submissions that were waiting for evaluation
