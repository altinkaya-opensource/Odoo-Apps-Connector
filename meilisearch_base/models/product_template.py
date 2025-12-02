from odoo import api, models, fields


class ProductTemplate(models.Model):
    _name = "product.template"
    _inherit = ["product.template", "meilisearch.document.mixin"]

    def _prepare_index_document(self):
        document = super()._prepare_index_document()
        categ_id = fields.first(self.public_categ_ids)
        document["category_name"] = categ_id.name
        document["category_full_name"] = categ_id.display_name
        document["sales_count"] = self.variant_sale_qty360days

        if self.default_code and self.default_code != "/":
            document["default_code"] = self.default_code
        else:
            document["default_code"] = ""
        document["description_sale"] = self.description_sale or ""
        document["full_description"] = str(self.public_description or "")
        document["brand_name"] = self.product_brand_id.name or ""
        document["attribute_names"] = self.attribute_line_ids.mapped(
            "value_ids.display_name"
        )
        document["feature_names"] = self.feature_line_ids.mapped(
            "value_ids.display_name"
        )
        document["search_keywords"] = self.search_keywords or ""

        return document

    def _get_index_document_filter(self):
        return (
            lambda r: r.active
            and r.is_published
            and r.sale_ok
            and r.categ_id.is_published
        )

    @api.depends(
        "name",
        "search_keywords",
        "categ_id",
        "variant_sale_qty360days",
        "attribute_line_ids",
        "feature_line_ids",
        "default_code",
        "description_sale",
        "public_description",
    )
    def _compute_index_document(self):
        return super()._compute_index_document()
