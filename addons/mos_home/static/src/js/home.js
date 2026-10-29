import { Component, onWillStart, onWillUnmount, proxy, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const REFRESH_MS = 60000;
const DONUT_COLOURS = ["#1a62d8", "#1b7a3e", "#9a5b00", "#6b3fa8", "#b42318", "#14589c"];
const STATE_TONES = { arrived: "blue", weighing: "amber", weighed: "amber", cleared: "green", done: "green", draft: "neutral" };

export class MosHome extends Component {
    static template = "mos_home.Home";
    props = useProps({ ...standardActionServiceProps });

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = proxy({ data: null, loading: false });
        onWillStart(() => this.load());
        const timer = setInterval(() => this.load(), REFRESH_MS);
        onWillUnmount(() => clearInterval(timer));
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("mos.overview", "get_home", []);
        } finally {
            this.state.loading = false;
        }
    }

    get greeting() {
        const hour = new Date().getHours();
        const part = hour < 12 ? _t("Good morning") : hour < 18 ? _t("Good afternoon") : _t("Good evening");
        return `${part}, ${this.state.data.user.split(" ")[0]}`;
    }

    fmt(value) {
        return Number(value || 0).toLocaleString("en-US");
    }

    tone(state) {
        return STATE_TONES[state] || "neutral";
    }

    /** The slices of the donut: each is a circle stroke with a dash as long as its share. */
    get slices() {
        const mix = this.state.data.grain_mix;
        const total = mix.reduce((sum, row) => sum + row.value, 0);
        let offset = 0;
        return mix.map((row, index) => {
            const share = total ? (row.value / total) * 100 : 0;
            const slice = { label: row.label, value: row.value, share: Math.round(share), dash: `${share} ${100 - share}`, offset: -offset, colour: DONUT_COLOURS[index % DONUT_COLOURS.length] };
            offset += share;
            return slice;
        });
    }

    get siloTotal() {
        return this.state.data.grain_mix.reduce((sum, row) => sum + row.value, 0);
    }

    async openCard(item) {
        if (item.method) {
            this.action.doAction(await this.orm.call("mos.overview", item.method, [[this.state.data.overview_id]]));
        } else if (item.xmlid) {
            this.action.doAction(await this.orm.call("mos.overview", "home_action", [item.xmlid]));
        }
    }

    async openTile(tile) {
        this.action.doAction(await this.orm.call("mos.overview", "home_action", [tile.xmlid, tile.new]));
    }

    openRecord(model, id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id, views: [[false, "form"]], target: "current" });
    }

    async openFigures() {
        this.action.doAction(await this.orm.call("mos.overview", "action_open_figures", []));
    }
}

registry.category("actions").add("mos_home.home", MosHome);
