# Copyright (C) 2025 Ahmet Yiğit Budak (https://github.com/yibudak)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MeilisearchIndexDocument(models.Model):
    _name = "meilisearch.index.document"
    _description = "Meilisearch Index Document"
    _order = "id desc"
    _rec_name = "display_name"

    # Core relationships
    index_id = fields.Many2one(
        "meilisearch.index",
        string="Index",
        required=True,
        ondelete="cascade",
        index=True,
    )
    lang_id = fields.Many2one(
        "res.lang",
        string="Language",
        required=True,
        ondelete="cascade",
        index=True,
    )
    res_model = fields.Char(
        string="Model",
        required=True,
        index=True,
    )
    res_id = fields.Many2oneReference(
        string="Record ID",
        model_field="res_model",
        required=True,
        index=True,
    )

    # Computed unique identifier for Meilisearch
    meilisearch_id = fields.Char(
        string="Meilisearch ID",
        compute="_compute_meilisearch_id",
        store=True,
        help="Unique document ID in format: {res_id}_{lang_code}",
    )

    # Document storage (moved from mixin)
    index_date = fields.Datetime(string="Index Date")
    index_document = fields.Json(
        string="Document",
        help="Stores the document as JSONB.",
    )
    index_document_hash = fields.Char(
        string="Document Hash",
        size=64,
    )
    index_document_read = fields.Text(
        string="Document (Read)",
        compute="_compute_index_document_read",
        help="Returns the document as JSON for display.",
    )
    index_result = fields.Selection(
        [
            ("pending", "Pending"),
            ("queued", "Queued"),
            ("indexed", "Indexed"),
            ("error", "Error"),
            ("not_found", "Not Found"),
            ("no_index", "No Index"),
        ],
        string="Result",
        default="pending",
        index=True,
    )
    index_response = fields.Text(
        string="Response",
        help="Response from Meilisearch index.",
    )

    # Display name
    display_name = fields.Char(
        compute="_compute_display_name",
        store=True,
    )

    _sql_constraints = [
        (
            "unique_document_per_lang",
            "UNIQUE(index_id, res_model, res_id, lang_id)",
            "Only one document per record per language per index is allowed.",
        ),
    ]

    @api.depends("res_id", "lang_id.code")
    def _compute_meilisearch_id(self):
        for doc in self:
            if doc.res_id and doc.lang_id:
                doc.meilisearch_id = f"{doc.res_id}_{doc.lang_id.code}"
            else:
                doc.meilisearch_id = False

    @api.depends("res_model", "res_id", "lang_id.code")
    def _compute_display_name(self):
        for doc in self:
            if doc.res_model and doc.res_id and doc.lang_id:
                doc.display_name = f"{doc.res_model},{doc.res_id} [{doc.lang_id.code}]"
            else:
                doc.display_name = f"Document #{doc.id}"

    def _compute_index_document_read(self):
        for record in self:
            if record.index_document:
                record.index_document_read = json.dumps(
                    record.index_document, indent=4, ensure_ascii=False
                )
            else:
                record.index_document_read = "{}"

    def get_source_record(self):
        """Get the source record with the appropriate language context."""
        self.ensure_one()
        if self.res_model and self.res_id and self.lang_id:
            return (
                self.env[self.res_model]
                .with_context(lang=self.lang_id.code)
                .browse(self.res_id)
            )
        return self.env[self.res_model].browse()

    def _update_to_meilisearch(self, index=None):
        """Send documents to Meilisearch."""
        if index is None:
            # Group by index for batch operations
            by_index = {}
            for doc in self:
                if doc.index_id not in by_index:
                    by_index[doc.index_id] = self.env["meilisearch.index.document"]
                by_index[doc.index_id] |= doc

            for idx, docs in by_index.items():
                docs._update_to_meilisearch(idx)
            return

        client = index.get_client()
        if not client:
            self.write(
                {"index_result": "no_index", "index_response": "No client available"}
            )
            return

        for offset in range(0, len(self), 20):
            batch = self[offset : offset + 20]
            try:
                with self.env.cr.savepoint():
                    documents = [doc.index_document for doc in batch]
                    res = client.index(index.index_name).update_documents(documents)

                    if index.create_task:
                        self.env["meilisearch.task"].create(
                            {
                                "name": "documentAdditionOrUpdate",
                                "index_id": index.id,
                                "uid": res.task_uid,
                                "document_ids": [rec.id for rec in batch],
                            }
                        )

                    batch.write(
                        {
                            "index_result": "queued",
                            "index_response": "Task enqueued",
                            "index_date": res.enqueued_at,
                        }
                    )
            except Exception as e:
                _logger.exception("Failed to update documents to Meilisearch")
                batch.write({"index_result": "error", "index_response": str(e)})

    def _delete_from_meilisearch(self, index=None):
        """Delete documents from Meilisearch."""
        if index is None:
            by_index = {}
            for doc in self:
                if doc.index_id not in by_index:
                    by_index[doc.index_id] = self.env["meilisearch.index.document"]
                by_index[doc.index_id] |= doc

            for idx, docs in by_index.items():
                docs._delete_from_meilisearch(idx)
            return

        client = index.get_client()
        if not client:
            return

        for offset in range(0, len(self), 20):
            batch = self[offset : offset + 20]
            try:
                with self.env.cr.savepoint():
                    # Use meilisearch_id for deletion
                    meilisearch_ids = batch.mapped("meilisearch_id")
                    filter_str = " OR ".join(
                        [f'id="{mid}"' for mid in meilisearch_ids if mid]
                    )
                    if not filter_str:
                        continue

                    res = client.index(index.index_name).delete_documents(
                        filter=filter_str
                    )

                    if index.create_task:
                        self.env["meilisearch.task"].create(
                            {
                                "name": "documentDeletion",
                                "index_id": index.id,
                                "uid": res.task_uid,
                                "document_ids": [rec.id for rec in batch],
                            }
                        )

                    batch.write(
                        {
                            "index_result": "queued",
                            "index_response": "Deletion queued",
                            "index_date": res.enqueued_at,
                        }
                    )
            except Exception as e:
                _logger.exception("Failed to delete documents from Meilisearch")
                batch.write({"index_result": "error", "index_response": str(e)})

    def _check_in_meilisearch(self, index=None):
        """Check if documents exist in Meilisearch."""
        if index is None:
            by_index = {}
            for doc in self:
                if doc.index_id not in by_index:
                    by_index[doc.index_id] = self.env["meilisearch.index.document"]
                by_index[doc.index_id] |= doc

            for idx, docs in by_index.items():
                docs._check_in_meilisearch(idx)
            return

        client = index.get_client()
        if not client:
            self.write({"index_result": "no_index", "index_response": "No client"})
            return

        for offset in range(0, len(self), 20):
            batch = self[offset : offset + 20]
            try:
                meilisearch_ids = batch.mapped("meilisearch_id")
                filter_str = " OR ".join(
                    [f'id="{mid}"' for mid in meilisearch_ids if mid]
                )
                if not filter_str:
                    continue

                res = client.index(index.index_name).search("", {"filter": filter_str})

                found_ids = {hit["id"] for hit in res.get("hits", [])}

                for doc in batch:
                    if doc.meilisearch_id in found_ids:
                        doc.write(
                            {"index_result": "indexed", "index_response": "Found in index"}
                        )
                    else:
                        doc.write(
                            {"index_result": "not_found", "index_response": "Not in index"}
                        )
            except Exception as e:
                _logger.exception("Failed to check documents in Meilisearch")
                batch.write({"index_result": "error", "index_response": str(e)})

    # Action methods
    def action_reindex(self):
        """Reindex selected documents."""
        for doc in self:
            source_record = doc.get_source_record()
            if source_record.exists():
                source_record._compute_index_document()
        return True

    def action_check(self):
        """Check if documents exist in Meilisearch."""
        self._check_in_meilisearch()
        return True

    def button_view_source(self):
        """Open the source record in a form view."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "context": {
                "create": True,
                "delete": True,
                "edit": True,
            },
        }
