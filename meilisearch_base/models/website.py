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
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class WebsiteSearchableMixin(models.AbstractModel):
    _inherit = "website.searchable.mixin"

    @api.model
    def _meilisearch_search_build_domain(self, domain_list, search, fields, extra=None):
        if search:
            index = (
                self.env["meilisearch.index"]
                .sudo()
                .get_matching_index(model=self._name)
            )
            client = index.get_client() if index else None
            if client:
                try:
                    meili_res = client.index(index.index_name).search(
                        search,
                        {
                            "limit": self.env.context.get("search_limit") or 25,
                            "hybrid": {"embedder": "default", "semanticRatio": 0.3},
                        },
                    )
                    hits = meili_res.get("hits", [])
                    ids = [int(hit["id"]) for hit in hits if hit.get("id")]
                    if len(ids) > 0:
                        return ids
                except Exception as exc:
                    _logger.exception(
                        "Meilisearch search failed for query '%s': %s", search, exc
                    )
        return False

    @api.model
    def _search_fetch(self, search_detail, search, limit, order):
        index = (
            self.env["meilisearch.index"].sudo().get_matching_index(model=self._name)
        )
        if index and search:
            self = self.with_context(search_limit=limit)
            fields = search_detail["search_fields"]
            base_domain = search_detail["base_domain"]
            matched_ids = self._meilisearch_search_build_domain(
                base_domain, search, fields, search_detail.get("search_extra")
            )
            if matched_ids:
                results = self.browse(matched_ids)
                count = len(matched_ids)
                return results, count

        return super()._search_fetch(search_detail, search, limit, order)
