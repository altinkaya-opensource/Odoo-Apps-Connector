import json
import logging
from hashlib import sha256

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MeilsearchDocumentMixin(models.AbstractModel):
    _name = "meilisearch.document.mixin"
    _description = "Meilisearch Document Mixin"

    name = fields.Char()

    # Link to index documents for all languages
    meilisearch_document_ids = fields.One2many(
        "meilisearch.index.document",
        "res_id",
        string="Meilisearch Documents",
        domain=lambda self: [("res_model", "=", self._name)],
    )

    # Computed summary field for convenience
    meilisearch_status = fields.Selection(
        [
            ("not_configured", "Not Configured"),
            ("pending", "Pending"),
            ("partial", "Partially Indexed"),
            ("indexed", "Fully Indexed"),
            ("error", "Has Errors"),
        ],
        string="Meilisearch Status",
        compute="_compute_meilisearch_status",
        store=True,
    )

    # Compute methods

    @api.depends("name")
    def _compute_index_document(self):
        """
        Main compute method - creates/updates index.document records
        for all configured languages.
        """
        index = self.env["meilisearch.index"].get_matching_index(model=self[:1]._name)
        if not index:
            return

        # Filter records that should be indexed
        filter_func = self._get_index_document_filter()
        index_records = self.filtered(filter_func)
        delete_records = self - index_records

        # Process indexable records
        if index_records:
            index._ensure_index_documents(index_records)
            self._sync_index_documents(index, index_records)

        # Handle records that should be deleted
        if delete_records:
            self._cleanup_index_documents(index, delete_records)

    def _sync_index_documents(self, index, records):
        """
        Update the index.document records for the given source records.
        Prepares documents in each language context and syncs to Meilisearch.
        """
        IndexDocument = self.env["meilisearch.index.document"]
        langs = index.get_active_languages()

        documents_to_update = IndexDocument.browse()

        for record in records:
            for lang in langs:
                # Find the index.document record
                doc_record = IndexDocument.search(
                    [
                        ("index_id", "=", index.id),
                        ("res_model", "=", record._name),
                        ("res_id", "=", record.id),
                        ("lang_id", "=", lang.id),
                    ],
                    limit=1,
                )

                if not doc_record:
                    continue

                # Prepare document in language context
                record_in_lang = record.with_context(lang=lang.code)
                document = record_in_lang._prepare_index_document()

                # Add language and meilisearch_id to document
                document["id"] = doc_record.meilisearch_id  # e.g., "42_en_US"
                document["source_id"] = record.id
                document["lang"] = lang.code

                # Compute hash
                document_hash = sha256(
                    json.dumps(document, sort_keys=True).encode()
                ).hexdigest()

                # Check if update needed
                if (
                    document_hash != doc_record.index_document_hash
                    or doc_record.index_result != "indexed"
                ):
                    doc_record.write(
                        {
                            "index_document": document,
                            "index_document_hash": document_hash,
                        }
                    )
                    documents_to_update |= doc_record

        # Batch update to Meilisearch
        if documents_to_update:
            documents_to_update._update_to_meilisearch(index)

    def _cleanup_index_documents(self, index, records):
        """Remove index.document records for records that no longer pass filter."""
        IndexDocument = self.env["meilisearch.index.document"]

        docs_to_delete = IndexDocument.search(
            [
                ("index_id", "=", index.id),
                ("res_model", "=", records[:1]._name),
                ("res_id", "in", records.ids),
            ]
        )

        if docs_to_delete:
            docs_to_delete._delete_from_meilisearch(index)
            docs_to_delete.unlink()

    @api.depends("meilisearch_document_ids.index_result")
    def _compute_meilisearch_status(self):
        """Compute overall indexing status across all languages."""
        for record in self:
            docs = record.meilisearch_document_ids
            if not docs:
                record.meilisearch_status = "not_configured"
            elif any(d.index_result == "error" for d in docs):
                record.meilisearch_status = "error"
            elif all(d.index_result == "indexed" for d in docs):
                record.meilisearch_status = "indexed"
            elif any(d.index_result == "indexed" for d in docs):
                record.meilisearch_status = "partial"
            else:
                record.meilisearch_status = "pending"

    # Model methods

    def check_index_document(self):
        """Check if documents exist in Meilisearch."""
        for record in self:
            record.meilisearch_document_ids._check_in_meilisearch()
        return True

    def update_index_document(self):
        """Force recomputation of index documents."""
        return self._compute_index_document()

    def delete_index_document(self):
        """Delete documents from Meilisearch."""
        for record in self:
            if record.meilisearch_document_ids:
                record.meilisearch_document_ids._delete_from_meilisearch()
                record.meilisearch_document_ids.unlink()
        return True

    def unlink(self):
        """Delete from Meilisearch before unlinking source record."""
        for record in self:
            if record.meilisearch_document_ids:
                record.meilisearch_document_ids._delete_from_meilisearch()
                record.meilisearch_document_ids.unlink()
        return super().unlink()

    # Action methods

    def button_view_document(self):
        return {
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": self._name,
            "res_id": self.id,
            "context": {
                "create": True,
                "delete": True,
                "edit": True,
            },
        }

    def button_view_index_documents(self):
        """Open index documents related to this record."""
        self.ensure_one()
        return {
            "name": f"Index Documents for {self.display_name}",
            "type": "ir.actions.act_window",
            "view_mode": "tree,form",
            "res_model": "meilisearch.index.document",
            "domain": [
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
            ],
            "context": {
                "create": False,
            },
        }

    # Private methods

    def _prepare_index_document(self):
        """
        Prepare the document for indexing.
        Called in language context - self already has lang in context.
        Override in subclasses to add model-specific fields.

        Note: "id", "source_id", and "lang" are added by _sync_index_documents
        """
        self.ensure_one()
        return {"name": self.name}

    def _get_index_document_filter(self):
        """Return a filter function to determine which records should be indexed."""
        return lambda r: True
