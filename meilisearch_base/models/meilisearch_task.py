import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class MeilisearchTask(models.Model):
    _name = "meilisearch.task"
    _description = "Meilisearch Task"
    _order = "uid desc"

    name = fields.Char(required=True)
    uid = fields.Integer("UID", required=True)
    status = fields.Selection(
        [
            ("enqueued", "Enqued"),
            ("processing", "Processing"),
            ("succeeded", "Succeeded"),
            ("failed", "Failed"),
        ],
        default="enqueued",
        required=True,
    )
    response = fields.Text()
    index_id = fields.Many2one("meilisearch.index", required=True)
    document_ids = fields.Char(help="Comma separated list of document ids.")

    def name_get(self):
        res = []
        for task in self:
            res.append(
                (
                    task.id,
                    _("%s - %s (%s)") % (task.name, task.index_id.model, task.uid),
                )
            )
        return res

    def _get_document_ids(self):
        """Get meilisearch.index.document records associated with this task."""
        self.ensure_one()
        doc_ids = safe_eval(self.document_ids)
        return self.env["meilisearch.index.document"].browse(doc_ids)

    def button_check_task(self):
        self.ensure_one()
        self.check_task()

    def button_view_documents(self):
        doc_ids = safe_eval(self.document_ids)
        return {
            "name": _("Index Documents"),
            "type": "ir.actions.act_window",
            "view_mode": "tree,form",
            "res_model": "meilisearch.index.document",
            "domain": [("id", "in", doc_ids)],
            "context": {
                "create": False,
            },
        }

    def task_succeeded(self):
        self.ensure_one()
        self.write(
            {
                "status": "succeeded",
                "response": "Task succeeded",
            }
        )
        if self.name == "documentAdditionOrUpdate":
            document_ids = self._get_document_ids()
            document_ids.write({"index_result": "indexed", "index_response": "Task succeeded"})
        if self.name == "documentDeletion":
            document_ids = self._get_document_ids()
            document_ids.write({"index_result": "not_found", "index_response": "Task succeeded"})

    def task_failed(self):
        self.ensure_one()
        self.write(
            {
                "status": "failed",
                "response": "Task failed",
            }
        )
        documents = self._get_document_ids()
        documents.write({"index_result": "error", "index_response": "Task failed"})

    def check_task(self):
        client = self.index_id.get_client()
        for task in self:
            index_task = client.get_task(task.uid)
            task.write({"status": index_task.status, "response": index_task})

    @api.autovacuum
    def _gc_meilisearch_tasks(self):
        """Delete tasks from active indexes after one day."""

        # Get all active indexes
        index_ids = self.env["meilisearch.index"].search(
            [
                ("active", "=", True),
                "|",
                ("database_filter", "=", False),
                ("database_filter", "=", self._cr.dbname),
            ]
        )
        unlink_task_ids = self.env["meilisearch.task"].search(
            [
                ("index_id", "in", index_ids.ids),
                ("create_date", "<", fields.Datetime.now() - timedelta(days=1)),
            ]
        )
        unlink_task_ids.unlink()
