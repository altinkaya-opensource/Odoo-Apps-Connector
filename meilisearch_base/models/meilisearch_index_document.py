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
from odoo import models, api, fields


class MeilisearchIndexDocument(models.Model):
    _name = "meilisearch.index.document"
    _description = "Meilisearch Index Document"
    _order = "id desc"

    index_id = fields.Many2one("meilisearch.index", required=True)
    model = fields.Char(required=True)
    res_id = fields.Integer("Resource ID", required=True)
    document = fields.Text("Document JSON", required=True)
    state = fields.Selection(
        [
            ("to_index", "To Index"),
            ("indexed", "Indexed"),
            ("to_remove", "To Remove"),
            ("removed", "Removed"),
            ("error", "Error"),
        ],
        default="to_index",
        required=True,
    )
    error_message = fields.Text("Error Message")

    @api.model
    def _get_index_document_model(self):
        return self.index_id.model
