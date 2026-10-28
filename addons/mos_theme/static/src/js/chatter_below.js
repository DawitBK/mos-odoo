import "@mail/chatter/web/form_renderer";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { FormRenderer } from "@web/views/form/form_renderer";

// The client's screens use the whole width, so the history of a record (the chatter) sits below the form, never beside it.
// Odoo decides this in two places: the renderer (which layout) and the controller (a row layout for very wide screens).
patch(FormRenderer.prototype, {
    mailLayout() {
        const layout = super.mailLayout(...arguments);
        return layout === "SIDE_CHATTER" ? "BOTTOM_CHATTER" : layout;
    },
});

patch(FormController.prototype, {
    get className() {
        const result = super.className;
        delete result["o_xxl_form_view h-100"];
        return result;
    },
});
