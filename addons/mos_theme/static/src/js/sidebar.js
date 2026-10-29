import { Component, proxy } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useBus, useService } from "@web/core/utils/hooks";
import { NavBar } from "@web/webclient/navbar/navbar";
import { WebClient } from "@web/webclient/webclient";

const MOS_ROOT = "mos_core.menu_mos_root";
const COLLAPSED_KEY = "mos.sidebar.collapsed";

// How the screens of MOS are grouped, in the order a grain moves through the mill and the money follows it.
// A menu that is not listed (a module added later) lands in "More", so nothing a person may open is ever hidden.
const GROUPS = [
    { label: _t("Work"), sections: ["mos_dashboard.menu_mos_overview", "mos_process_map.menu_mos_process_map", "mos_approvals.menu_mos_approvals"] },
    { label: _t("Buy and receive"), sections: ["mos_procurement.menu_mos_procurement", "mos_gate.menu_mos_gate", "mos_lab.menu_mos_lab", "mos_flows.menu_mos_flows"] },
    { label: _t("Make and store"), sections: ["mos_silo.menu_mos_stock", "mos_production.menu_mos_production", "mos_inventory.menu_mos_inventory"] },
    { label: _t("Sell and get paid"), sections: ["mos_sales.menu_mos_sales", "mos_payables.menu_mos_payables", "mos_finance_plus.menu_mos_finance"] },
    { label: _t("Control"), sections: ["mos_controls.menu_mos_controls", "mos_core.menu_mos_config"] },
];

const SECTION_ICONS = {
    "mos_dashboard.menu_mos_overview": "chart",
    "mos_process_map.menu_mos_process_map": "transfer",
    "mos_approvals.menu_mos_approvals": "check",
    "mos_procurement.menu_mos_procurement": "cart",
    "mos_gate.menu_mos_gate": "truck",
    "mos_lab.menu_mos_lab": "flask",
    "mos_flows.menu_mos_flows": "arrow-in",
    "mos_silo.menu_mos_stock": "sack",
    "mos_production.menu_mos_production": "factory",
    "mos_inventory.menu_mos_inventory": "cube",
    "mos_sales.menu_mos_sales": "users",
    "mos_payables.menu_mos_payables": "receipt",
    "mos_finance_plus.menu_mos_finance": "bank",
    "mos_controls.menu_mos_controls": "shield",
    "mos_core.menu_mos_config": "list",
};

function readCollapsed() {
    try {
        return browser.localStorage.getItem(COLLAPSED_KEY) === "1";
    } catch {
        return false;
    }
}

/** Every screen below a menu, in order; a sub-section becomes a small heading above its screens. */
function entriesOf(node) {
    const out = [];
    for (const child of node.childrenTree || []) {
        if (child.childrenTree && child.childrenTree.length) {
            out.push({ type: "label", key: `l${child.id}`, name: child.name });
            out.push(...entriesOf(child));
        } else if (child.actionID) {
            out.push({ type: "link", key: `m${child.id}`, menu: child });
        }
    }
    return out;
}

export class MosSidebar extends Component {
    static template = "mos_theme.Sidebar";

    setup() {
        this.menuService = useService("menu");
        this.actionService = useService("action");
        this.state = proxy({ open: null, query: "", collapsed: readCollapsed(), activeMenu: null, actionId: null });
        useBus(this.env.bus, "ACTION_MANAGER:UI-UPDATED", () => this.syncActive());
    }

    get app() {
        // At the landing page (/odoo) Odoo has not chosen an app yet; a MOS person lands on MOS.
        return this.menuService.getCurrentApp() || this.menuService.getApps().find((app) => app.xmlid === MOS_ROOT) || null;
    }

    get isMos() {
        return this.app && this.app.xmlid === MOS_ROOT;
    }

    /** The sections of the current app: [{label, items: [{id, name, icon, menu|null, entries}]}] */
    get groups() {
        const app = this.app;
        if (!app) {
            return [];
        }
        const tree = this.menuService.getMenuAsTree(app.id);
        const nodes = (tree.childrenTree || []).filter((node) => node.actionID || (node.childrenTree && node.childrenTree.length));
        const toItem = (node) => {
            const entries = entriesOf(node);
            return {
                id: node.id,
                name: node.name,
                icon: SECTION_ICONS[node.xmlid] || "doc",
                menu: node.actionID && !entries.length ? node : null,
                entries,
            };
        };
        if (!this.isMos) {
            return [{ label: "", items: nodes.map(toItem) }];
        }
        const byXmlid = new Map(nodes.map((node) => [node.xmlid, node]));
        const used = new Set();
        const groups = GROUPS.map((group) => ({
            label: group.label,
            items: group.sections.filter((xmlid) => byXmlid.has(xmlid)).map((xmlid) => (used.add(xmlid), toItem(byXmlid.get(xmlid)))),
        }));
        const rest = nodes.filter((node) => !used.has(node.xmlid));
        if (rest.length) {
            groups.push({ label: _t("More"), items: rest.map(toItem) });
        }
        return groups.filter((group) => group.items.length);
    }

    /** What the filter box finds: every screen of the app whose name or section contains the words typed. */
    get found() {
        const words = this.state.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
        if (!words.length) {
            return [];
        }
        const hits = [];
        for (const group of this.groups) {
            for (const item of group.items) {
                const entries = item.menu ? [{ type: "link", key: `m${item.menu.id}`, menu: item.menu }] : item.entries;
                for (const entry of entries) {
                    if (entry.type !== "link") {
                        continue;
                    }
                    const text = `${entry.menu.name} ${item.name}`.toLowerCase();
                    if (words.every((word) => text.includes(word))) {
                        hits.push({ key: entry.key, menu: entry.menu, section: item.name });
                    }
                }
            }
        }
        return hits.slice(0, 30);
    }

    /** The screen being shown; until one is chosen it is the first screen of the app, which is what the landing page opens. */
    get activeMenuId() {
        if (this.state.activeMenu) {
            return this.state.activeMenu;
        }
        const first = this.groups.flatMap((group) => group.items).find((item) => item.menu);
        return first ? first.menu.id : null;
    }

    isActive(menu) {
        return this.activeMenuId === menu.id;
    }

    isOpen(item) {
        return this.state.open === item.id;
    }

    /** Opens the section that holds a screen, so the person always sees where they are. */
    revealSectionOf(menuId) {
        for (const group of this.groups) {
            for (const item of group.items) {
                if (item.entries.some((entry) => entry.type === "link" && entry.menu.id === menuId)) {
                    this.state.open = item.id;
                    return;
                }
            }
        }
    }

    href(menu) {
        return `/odoo/action-${menu.actionID}`;
    }

    syncActive() {
        const controller = this.actionService.currentController;
        const actionId = controller && controller.action ? controller.action.id : null;
        if (!actionId) {
            return;
        }
        this.state.actionId = actionId;
        const app = this.app;
        if (!app) {
            return;
        }
        const current = this.state.activeMenu && this.menuService.getMenu(this.state.activeMenu);
        if (current && current.actionID === actionId) {
            return;
        }
        const match = this.menuService.getAll().find((menu) => menu.appID === app.id && menu.actionID === actionId);
        if (match) {
            this.state.activeMenu = match.id;
            this.revealSectionOf(match.id);
        }
    }

    async open(menu, ev) {
        if (ev && (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button === 1)) {
            return; // let the browser open the link in a new tab
        }
        ev && ev.preventDefault();
        this.state.activeMenu = menu.id;
        this.state.query = "";
        this.revealSectionOf(menu.id);
        await this.menuService.selectMenu(menu);
    }

    toggle(item) {
        if (this.state.collapsed) {
            this.setCollapsed(false);
        }
        this.state.open = this.state.open === item.id ? null : item.id;
    }

    onSearch(ev) {
        this.state.query = ev.target.value;
    }

    setCollapsed(value) {
        this.state.collapsed = value;
        try {
            browser.localStorage.setItem(COLLAPSED_KEY, value ? "1" : "0");
        } catch {
            // private window: the choice is kept for this page only
        }
    }

    goHome() {
        const home = this.groups.flatMap((group) => group.items).find((item) => item.menu);
        if (home) {
            this.open(home.menu);
        }
    }
}

patch(WebClient, {
    components: { ...WebClient.components, MosSidebar },
});

// The sections of the current app are in the side menu, so the top bar keeps only the apps button, the breadcrumbs and the systray.
patch(NavBar.prototype, {
    get currentAppSections() {
        return this.ui && this.ui.isSmall ? super.currentAppSections : [];
    },
});
