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
from odoo.osv import expression

_logger = logging.getLogger(__name__)

# Upper bound for Meilisearch results to ensure we have enough candidates
# after applying Odoo domain filters
_MEILI_MAX_LIMIT = 1000


class WebsiteSearchableMixin(models.AbstractModel):
    _inherit = "website.searchable.mixin"

    @api.model
    def _meilisearch_search(self, search, limit):
        """Query Meilisearch and return matching record IDs.

        :param search: search term
        :param limit: maximum number of results to request from Meilisearch
        :return: list of record IDs or False if Meilisearch is unavailable
        """
        if not search:
            return False
        index = (
            self.env["meilisearch.index"]
            .sudo()
            .get_matching_index(model=self._name)
        )
        client = index.get_client() if index else None
        if not client:
            return False
        try:
            current_lang = self.env.context.get("lang", "en_US")
            meili_res = client.index(index.index_name).search(
                search,
                {
                    "limit": limit or _MEILI_MAX_LIMIT,
                    "filter": f'lang = "{current_lang}"',
                    "hybrid": {"embedder": "default", "semanticRatio": 0.5},
                },
            )
            hits = meili_res.get("hits", [])
            ids = [
                int(hit["source_id"]) for hit in hits if hit.get("source_id")
            ]
            return ids if ids else False
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
            meili_limit = limit or _MEILI_MAX_LIMIT
            matched_ids = self._meilisearch_search(search, meili_limit)
            if matched_ids:
                # Combine Meilisearch candidate IDs with Odoo's base_domain
                # to respect all filters (published, category, price, etc.)
                base_domain = search_detail["base_domain"]
                domain = expression.AND(
                    base_domain + [[("id", "in", matched_ids)]]
                )
                model = self.sudo() if search_detail.get("requires_sudo") else self
                results = model.search(
                    domain,
                    limit=limit,
                    order=search_detail.get("order", order),
                )
                count = model.search_count(domain)
                return results, count

        return super()._search_fetch(search_detail, search, limit, order)
