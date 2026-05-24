import logging


logger = logging.getLogger("mlflow.tracing.export.async_export_queue")
logger.setLevel(logging.CRITICAL)
logger.disabled = True
logger.propagate = False

try:
    import mlflow.tracing.export.async_export_queue as _async_queue

    _async_queue._logger.disabled = True
    _async_queue.AsyncTraceExportQueue._at_exit_callback = lambda self: None
except Exception:
    pass
