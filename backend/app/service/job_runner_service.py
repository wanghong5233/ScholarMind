from __future__ import annotations
from typing import Type

from models.job import Job, JobStatus, JobType
from service.job_service import job_service
from utils.database import SessionLocal
from service.job_handler.interfaces import BaseJobHandler
from utils.get_logger import log

def execute_job(job_id: int, handler_cls: Type[BaseJobHandler]):
    """
    通用 Job 执行器：
    - 管理 Job 生命周期（状态更新、错误处理）
    - 调用具体的 Handler 执行业务逻辑
    - 处理后续任务的触发
    """
    db = SessionLocal()
    handler = handler_cls()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        job_service.update_progress(db, job_id=job.id, user_id=job.user_id, status=JobStatus.RUNNING.value, progress=0)
        try:
            log.info(f"JobRunner: start handler={handler_cls.__name__} job_id={job.id} kb_id={job.knowledge_base_id} user_id={job.user_id}")
        except Exception:
            pass

        result = handler.run(db=db, user_id=job.user_id, kb_id=job.knowledge_base_id, payload=job.payload or {})
        try:
            log.info(f"JobRunner: handler finished id={job.id} succeeded={result.succeeded} failed={result.failed} total={result.total}")
        except Exception:
            pass

        final_status = (
            JobStatus.SUCCESS.value
            if result.failed == 0
            else (JobStatus.PARTIAL.value if result.succeeded > 0 else JobStatus.FAILED.value)
        )
        job_service.update_progress(
            db,
            job_id=job.id,
            user_id=job.user_id,
            status=final_status,
            progress=100,
            total=result.total,
            succeeded=result.succeeded,
            failed=result.failed,
        )

        job = db.query(Job).filter(Job.id == job.id).first()
        if job:
            payload = job.payload or {}
            payload["resultDetails"] = result.details
            job.payload = payload
            db.add(job)
            db.commit()

        # 触发后续解析任务
        if result.doc_ids_to_parse:
            from service.job_handler.parse_index_handler import ParseIndexHandler
            parse_job = job_service.create_job(
                db,
                user_id=job.user_id,
                kb_id=job.knowledge_base_id,
                type=JobType.PARSE_INDEX.value,
                payload={"fromJobId": job.id, "docs": result.doc_ids_to_parse, "sessionId": (job.payload or {}).get("sessionId")},
            )
            try:
                log.info(f"JobRunner: schedule ParseIndexHandler parse_job_id={parse_job.id} docs={result.doc_ids_to_parse}")
            except Exception:
                pass
            # 注意：这里直接在当前后台任务中执行，未来可优化为独立任务
            execute_job(job_id=parse_job.id, handler_cls=ParseIndexHandler)

    except Exception as e:
        # 无法读取 job.user_id 时无法更新权限校验的进度，这里尽力而为
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job_service.update_progress(db, job_id=job_id, user_id=job.user_id, status=JobStatus.FAILED.value, error=str(e))
        finally:
            pass
    finally:
        db.close()
