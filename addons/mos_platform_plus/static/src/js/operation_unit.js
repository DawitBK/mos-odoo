import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const ACTION = "mos_platform_plus.action_mos_operation_unit";

/** The operation unit in the user menu: shows where the person works and opens the picker (only for a person with several units). */
function operationUnitItem() {
    const action = useService("action");
    const info = session.mos_operation_unit;
    return {
        type: "item",
        id: "mos_operation_unit",
        description: info ? `${_t("Operation unit")}: ${info.name}` : _t("Operation unit"),
        hide: !info || !info.several,
        callback: () => action.doAction(ACTION),
        sequence: 15,
    };
}
registry.category("user_menuitems").add("mos_operation_unit", operationUnitItem);

/** After sign-in a person with several operation units is asked which one they work at (once for each sign-in). */
registry.category("services").add("mos_operation_unit", {
    dependencies: ["action", "orm"],
    start(env, { action, orm }) {
        const info = session.mos_operation_unit;
        if (info && info.pick) {
            setTimeout(async () => {
                await orm.call("mos.operation.unit", "mark_asked", []);
                action.doAction(ACTION);
            }, 1500);
        }
    },
});
