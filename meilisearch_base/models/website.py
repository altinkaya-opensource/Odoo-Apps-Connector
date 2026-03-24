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

# Max results for full-page search (e.g. /shop). Kept moderate to avoid
# flooding results with loose semantic matches.
_MEILI_SHOP_LIMIT = 200


class WebsiteSearchableMixin(models.AbstractModel):
    _inherit = "website.searchable.mixin"

    @api.model
    def _meilisearch_search(self, search, limit):
        """Query Meilisearch and return matching record IDs.

        When limit is explicitly set (autocomplete/quick search), uses hybrid
        search (keyword + semantic). When limit is None (shop/full page search),
        uses keyword-only search to avoid loose semantic matches flooding results.

        :param search: search term
        :param limit: maximum number of results, None for full-page search
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
            search_params = {
                "filter": f'lang = "{current_lang}"',
            }
            if limit:
                # Autocomplete / quick search: hybrid (keyword + semantic)
                search_params["limit"] = limit
                search_params["hybrid"] = {
                    "embedder": "default",
                    "semanticRatio": 0.5,
                }
            else:
                # Full-page search (/shop): keyword-only to keep results precise
                search_params["limit"] = _MEILI_SHOP_LIMIT

            meili_res = client.index(index.index_name).search(
                search, search_params
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
            matched_ids = self._meilisearch_search(search, limit)
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
