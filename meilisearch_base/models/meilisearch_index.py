import json
import logging

import meilisearch

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MeilisearchIndex(models.Model):
    _name = "meilisearch.index"
    _description = "Meilisearch Index"
    _order = "sequence, active, id"

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=False)
    name = fields.Char(required=True)
    index_name = fields.Char(required=True)
    database_filter = fields.Char(help="If set the index is only active on matching databases.")
    model_id = fields.Many2one(
        "ir.model",
        required=True,
        help="Select the model this index should be active for.",
        ondelete="cascade",
    )
    model = fields.Char(string="Model Name", related="model_id.model", store=True)
    index_settings = fields.Text(
        required=True,
        default="""{
    "filterableAttributes": [
        "id",
        "source_id",
        "lang"
    ],
    "sortableAttributes": [
        "source_id"
    ]
}""",
    )
    create_task = fields.Boolean(
        string="Create Tasks",
        default=True,
        help="Create tasks to track document addition and update.",
    )
    lang_ids = fields.Many2many(
        "res.lang",
        string="Languages",
        help="Languages to index. If empty, only the default language is used.",
        domain=[("active", "=", True)],
    )
    task_ids = fields.One2many("meilisearch.task", "index_id")
    document_ids = fields.One2many(
        "meilisearch.index.document",
        "index_id",
        string="Index Documents",
    )
    task_count = fields.Integer(compute="_compute_task_count", store=True)

    document_filtered_count = fields.Integer(string="Documents Filtered", compute="_compute_document_count", store=True)
    document_queued_count = fields.Integer(string="Documents Queued", compute="_compute_document_count", store=True)
    document_indexed_count = fields.Integer(string="Documents Indexed", compute="_compute_document_count", store=True)
    document_error_count = fields.Integer(string="Documents Error", compute="_compute_document_count", store=True)
    document_not_found_count = fields.Integer(
        string="Documents Not Found", compute="_compute_document_count", store=True
    )
    document_no_index_count = fields.Integer(string="Documents No Index", compute="_compute_document_count", store=True)
    task_enqueued_count = fields.Integer(string="Task Enqueued", compute="_compute_task_count", store=True)
    task_processing_count = fields.Integer(string="Task Processing", compute="_compute_task_count", store=True)
    task_succeeded_count = fields.Integer(string="Task Succeeded", compute="_compute_task_count", store=True)
    task_failed_count = fields.Integer(string="Task Failed", compute="_compute_task_count", store=True)
    meilisearch_index_url = fields.Char(
        compute="_compute_meilisearch_index_url",
    )

    def _compute_meilisearch_index_url(self):
        for index in self:
            url = self.env["ir.config_parameter"].sudo().get_param("meilisearch.api_url")
            index.meilisearch_index_url = url

    def _compute_document_count(self):
        IndexDocument = self.env["meilisearch.index.document"]
        for index in self:
            if index.active and index.model in self.env:
                groups = IndexDocument.read_group(
                    [("index_id", "=", index.id)],
                    ["index_result"],
                    ["index_result"],
                )
                index.document_filtered_count = IndexDocument.search_count(
                    [("index_id", "=", index.id)]
                )

                def get_status_count(status):
                    matching = [g for g in groups if g["index_result"] == status]
                    return matching[0].get("index_result_count", 0) if matching else 0

                index.document_queued_count = get_status_count("queued")
                index.document_indexed_count = get_status_count("indexed")
                index.document_error_count = get_status_count("error")
                index.document_not_found_count = get_status_count("not_found")
                index.document_no_index_count = get_status_count("no_index")
            else:
                index.document_filtered_count = 0
                index.document_queued_count = 0
                index.document_indexed_count = 0
                index.document_error_count = 0
                index.document_not_found_count = 0
                index.document_no_index_count = 0

    def _compute_task_count(self):
        for index in self:
            if index.active and index.model in self.env:
                model = self.env[index.model]
                groups = self.env["meilisearch.task"].read_group([("index_id", "=", index.id)], ["status"], ["status"])
                index.document_filtered_count = model.search_count([])

                def get_status_count(status):
                    matching = [g for g in groups if g["status"] == status]
                    return matching[0].get("status_count", 0) if matching else 0

                index.task_enqueued_count = get_status_count("enqueued")
                index.task_processing_count = get_status_count("processing")
                index.task_succeeded_count = get_status_count("succeeded")
                index.task_failed_count = get_status_count("failed")
            else:
                index.task_enqueued_count = 0
                index.task_processing_count = 0
                index.task_succeeded_count = 0
                index.task_failed_count = 0

    @api.model
    def _cron_check_documents(self):
        # Get all active indexes
        for index in self.search(
            [
                ("active", "=", True),
                "|",
                ("database_filter", "=", ""),
                ("database_filter", "=", self._cr.dbname),
            ]
        ):
            _logger.info("Checking documents for index: %s", index.name)
            index.check_all_documents()

    def copy(self, default=None):
        self.ensure_one()
        default = default or {}
        if not default.get("name"):
            default["name"] = _("%s (copy)", self.name)
        return super().copy(default)

    def get_client(self):
        """Return the Meilisearch client."""
        icp = self.env["ir.config_parameter"].sudo()
        url = icp.get_param("meilisearch.api_url")
        api_key = icp.get_param("meilisearch.api_key")

        if not url or not api_key:
            _logger.error(_("Meilisearch URL and API key need to be configured " "in the system parameters."))
            return

        return meilisearch.Client(
            url=url,
            api_key=api_key,
            timeout=10,
        )

    @api.model
    def get_matching_index(self, model):
        index = self.search(
            [
                "&",
                ("active", "=", True),
                ("model", "=", model),
                "|",
                "|",
                ("database_filter", "=", ""),
                ("database_filter", "=", False),
                ("database_filter", "=", self._cr.dbname),
            ],
            limit=1,
        )
        return index

    def get_active_languages(self):
        """Return languages to index. Falls back to default language."""
        self.ensure_one()
        if self.lang_ids:
            return self.lang_ids
        # Fallback to default language
        default_lang = self.env["res.lang"].search([("code", "=", "en_US")], limit=1)
        return default_lang or self.env["res.lang"].search(
            [("active", "=", True)], limit=1
        )

    def _ensure_index_documents(self, records):
        """
        Ensure meilisearch.index.document records exist for all
        (record, language) combinations.
        """
        IndexDocument = self.env["meilisearch.index.document"]
        langs = self.get_active_languages()

        for record in records:
            for lang in langs:
                existing = IndexDocument.search(
                    [
                        ("index_id", "=", self.id),
                        ("res_model", "=", record._name),
                        ("res_id", "=", record.id),
                        ("lang_id", "=", lang.id),
                    ],
                    limit=1,
                )

                if not existing:
                    IndexDocument.create(
                        {
                            "index_id": self.id,
                            "res_model": record._name,
                            "res_id": record.id,
                            "lang_id": lang.id,
                        }
                    )

    def _remove_obsolete_documents(self, records, valid_lang_ids=None):
        """
        Remove index.document records for languages no longer configured
        or for records that no longer pass the filter.
        """
        IndexDocument = self.env["meilisearch.index.document"]

        domain = [
            ("index_id", "=", self.id),
            ("res_model", "=", records[:1]._name),
            ("res_id", "in", records.ids),
        ]

        if valid_lang_ids is not None:
            domain.append(("lang_id", "not in", valid_lang_ids.ids))

        obsolete = IndexDocument.search(domain)
        if obsolete:
            obsolete._delete_from_meilisearch()
            obsolete.unlink()

    def button_sync_languages(self):
        """
        Sync index.document records with current language configuration.
        Creates missing documents and removes obsolete ones.
        """
        self.ensure_one()
        if not self.model or self.model not in self.env:
            return

        model = self.env[self.model]
        all_records = model.search([])

        if not all_records:
            return

        # Get filter to identify indexable records
        filter_func = all_records._get_index_document_filter()
        indexable_records = all_records.filtered(filter_func)
        non_indexable = all_records - indexable_records

        # Ensure documents exist for indexable records
        if indexable_records:
            self._ensure_index_documents(indexable_records)

        # Remove documents for non-indexable records
        if non_indexable:
            self._remove_obsolete_documents(non_indexable)

        # Remove documents for languages no longer configured
        if indexable_records:
            self._remove_obsolete_documents(
                indexable_records, valid_lang_ids=self.get_active_languages()
            )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Languages Synced"),
                "message": _("Index documents have been synchronized with language configuration."),
                "sticky": False,
                "type": "success",
            },
        }

    def button_view_documents(self):
        return {
            "name": _("Index Documents"),
            "type": "ir.actions.act_window",
            "view_mode": "tree,form",
            "res_model": "meilisearch.index.document",
            "domain": [("index_id", "=", self.id)],
            "context": {
                "search_default_group_by_index_result": True,
                "default_index_id": self.id,
                "create": False,
            },
        }

    def button_view_tasks(self):
        return {
            "name": "Index Tasks",
            "type": "ir.actions.act_window",
            "view_mode": "tree,form",
            "views": [(False, "tree"), (False, "form")],
            "res_model": "meilisearch.task",
            "domain": [("index_id", "=", self.id)],
        }

    def button_check_index(self):
        return self._get_index()

    def button_create_index(self):
        return self._create_index()

    def button_update_index(self):
        return self._update_index()

    def button_delete_index(self):
        return self._delete_index()

    def button_check_all_documents(self):
        return self.check_all_documents()

    def button_check_all_tasks(self):
        return self.check_all_tasks()

    def button_check_api_key(self):
        return self._get_version()

    def button_open_meilisearch_index_url(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "target": "_blank",
            "url": self.meilisearch_index_url,
        }

    def check_all_documents(self):
        """
        Check all documents in index that are not indexed.
        """
        self.ensure_one()
        IndexDocument = self.env["meilisearch.index.document"]

        # Get index documents that are not indexed
        documents = IndexDocument.search(
            [
                ("index_id", "=", self.id),
                ("index_result", "!=", "indexed"),
            ]
        )
        documents._check_in_meilisearch(self)
        self._compute_document_count()

    def check_all_tasks(self):
        """
        Check all tasks of index that are enqueued.
        """
        self.ensure_one()
        task_ids = self.env["meilisearch.task"].search(
            [
                ("status", "=", "enqueued"),
                ("index_id", "=", self.id),
            ]
        )
        task_ids.check_task()
        self._compute_task_count()

    def _get_version(self):
        self.ensure_one()
        client = self.get_client()
        if client:
            try:
                url = (self.env["ir.config_parameter"].sudo().get_param("meilisearch.api_url"),)
                client.health()
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Meilisearch API Key"),
                        "message": _("The Meilisearch API key for '%s' works.", url),
                        "sticky": False,
                        "type": "success",
                    },
                }
            except Exception as e:
                raise UserError(_("The Meilisearch API key for '%s' does not work: %s", url, e)) from None

    def _get_index(self):
        self.ensure_one()
        client = self.get_client()
        if client:
            try:
                client.get_index(self.index_name)
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Meilisearch Index"),
                        "message": _("The Meilisearch index '%s' was found.", self.index_name),
                        "sticky": False,
                        "type": "success",
                    },
                }
            except Exception as e:
                raise UserError(
                    _(
                        "Could not get Meilisearch index '%s': %s",
                        self.index_name,
                        e,
                    )
                ) from None

    def _create_index(self):
        self.ensure_one()
        client = self.get_client()
        if client:
            try:
                client.create_index(self.index_name, {"primaryKey": "id"})
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Meilisearch Index Created"),
                        "message": _(
                            "The Meilisearch index '%s' has been created.",
                            self.index_name,
                        ),
                        "sticky": False,
                        "type": "success",
                    },
                }
            except Exception as e:
                raise UserError(
                    _(
                        "Could not create the Meilisearch index '%s': %s",
                        self.index_name,
                        e.message,
                    )
                ) from None

    def _update_index(self):
        self.ensure_one()
        client = self.get_client()
        if client:
            try:
                client.index(self.index_name).update_settings(json.loads(self.index_settings))
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Meilisearch Index Created"),
                        "message": _(
                            "The Meilisearch index settings updated for '%s'.",
                            self.index_name,
                        ),
                        "sticky": False,
                        "type": "success",
                    },
                }
            except Exception as e:
                raise UserError(
                    _(
                        "Could not create the Meilisearch index '%s': %s",
                        self.index_name,
                        e.message,
                    )
                ) from None

    def _delete_index(self):
        self.ensure_one()
        client = self.get_client()
        if client:
            try:
                client.delete_index(self.index_name)
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Meilisearch Index Deleted"),
                        "message": _("The Meilisearch index '%s' was deleted.", self.index_name),
                        "sticky": False,
                        "type": "success",
                    },
                }
            except Exception as e:
                raise UserError(
                    _(
                        "Could not delete the Meilisearch index '%s': %s",
                        self.index_name,
                        e.message,
                    )
                ) from None
