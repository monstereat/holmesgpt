(() => {
  const tokenKey = "holmes-aiops-session";
  const token = () => sessionStorage.getItem(tokenKey);
  const loginPanel = document.getElementById("login-panel");
  const workbench = document.getElementById("workbench");
  const loginMessage = document.getElementById("login-message");
  const pageMessage = document.getElementById("page-message");
  const listState = document.getElementById("list-state");
  const table = document.getElementById("incident-table");
  const detail = document.getElementById("incident-detail");
  const oidcLogin = document.getElementById("oidc-login");
  const userAdmin = document.getElementById("user-admin");
  const userAdminMessage = document.getElementById("user-admin-message");
  let authMode = "local";
  let principal = null;

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (token()) headers.set("Authorization", `Bearer ${token()}`);
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    if (response.status === 401) {
      const wasAuthenticated = Boolean(token()) || authMode === "oidc";
      sessionStorage.removeItem(tokenKey);
      if (wasAuthenticated) showLogin("登录已过期，请重新登录。");
      throw new Error("登录已过期");
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `请求失败 (${response.status})`);
    return body;
  }

  function showLogin(message = "") {
    loginPanel.classList.remove("hidden");
    workbench.classList.add("hidden");
    loginMessage.textContent = message;
    document.getElementById("identity").textContent = "";
  }

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function showError(target, error) {
    target.textContent = error instanceof Error ? error.message : "发生未知错误";
  }

  const severityLabels = { critical: "严重", high: "高", medium: "中", low: "低" };
  const statusLabels = {
    open: "未处理",
    investigating: "调查中",
    awaiting_approval: "等待审批",
    resolved: "已解决",
    closed: "已关闭",
  };
  const statusTransitions = {
    open: ["investigating", "closed"],
    investigating: ["awaiting_approval", "resolved", "closed"],
    awaiting_approval: ["investigating", "resolved", "closed"],
    resolved: ["investigating", "closed"],
    closed: [],
  };

  async function loadIncidents() {
    pageMessage.textContent = "";
    listState.textContent = "正在加载事故…";
    listState.classList.remove("hidden");
    table.classList.add("hidden");
    try {
      const status = document.getElementById("status-filter").value;
      const query = status ? `?status=${encodeURIComponent(status)}` : "";
      const result = await api(`/api/incidents${query}`);
      const rows = document.getElementById("incident-rows");
      rows.replaceChildren();
      if (!result.items.length) {
        listState.textContent = "当前没有符合条件的事故。";
        return;
      }
      for (const incident of result.items) {
        const row = document.createElement("tr");
        row.dataset.id = incident.id;
        row.append(
          el("td", incident.alert_name),
          el("td", statusLabels[incident.status] || incident.status, "status"),
          el("td", severityLabels[incident.severity] || incident.severity),
          el("td", incident.assignee_username || "未指派"),
          el("td", (incident.trace_ids || []).join(", ") || "无 Trace"),
        );
        row.addEventListener("click", () => loadIncident(incident.id));
        rows.append(row);
      }
      listState.classList.add("hidden");
      table.classList.remove("hidden");
    } catch (error) {
      showError(pageMessage, error);
      listState.textContent = "事故列表加载失败。请重试。";
    }
  }

  async function loadUsers() {
    userAdminMessage.textContent = "";
    try {
      const result = await api("/api/users");
      const rows = document.getElementById("user-rows");
      rows.replaceChildren();
      for (const user of result.items) {
        const row = document.createElement("tr");
        row.append(
          el("td", user.username),
          el("td", user.role),
          el("td", (user.resource_scopes || []).join(", ") || "无"),
          el("td", user.active ? "启用" : user.reactivation_requested_at ? "已停用 · 待恢复" : "已停用", "status"),
        );
        const action = el("td");
        const pendingReactivation = !user.active && Boolean(user.reactivation_requested_at);
        const button = el("button", user.active ? "停用" : pendingReactivation ? "恢复" : "已停用");
        button.type = "button";
        button.disabled = user.active ? user.id === principal.user_id : !pendingReactivation || user.id === principal.user_id;
        button.addEventListener("click", async () => {
          if (!window.confirm(user.active ? `停用 ${user.username} 的工作台访问？该操作不会移除其身份提供商账号。` : `批准恢复 ${user.username} 的工作台访问？角色和资源范围仍由身份提供商组映射决定。`)) return;
          button.disabled = true;
          try {
            const endpoint = user.active ? "disable" : "reactivate";
            await api(`/api/users/${encodeURIComponent(user.id)}/${endpoint}`, { method: "POST", ...(!user.active ? { body: "{}" } : {}) });
            await loadUsers();
          } catch (error) {
            showError(userAdminMessage, error);
            button.disabled = false;
          }
        });
        action.append(button);
        row.append(action);
        rows.append(row);
      }
    } catch (error) {
      showError(userAdminMessage, error);
    }
  }

  async function loadIncident(id) {
    detail.replaceChildren(el("p", "正在加载事故详情…", "muted"));
    try {
      const incident = await api(`/api/incidents/${encodeURIComponent(id)}`);
      detail.replaceChildren();
      detail.append(el("h2", incident.alert_name));
      detail.append(el("p", `${statusLabels[incident.status] || incident.status} · ${new Date(incident.created_at).toLocaleString()} · Trace: ${(incident.trace_ids || []).join(", ") || "无"}`, "muted"));
      const summary = document.createElement("section");
      summary.append(el("h3", "告警信息"), el("pre", JSON.stringify(incident.summary || {}, null, 2)));
      detail.append(summary);

      const triage = document.createElement("section");
      triage.append(el("h3", "分级与负责人"));
      triage.append(el("p", `严重度：${severityLabels[incident.severity] || incident.severity} · 负责人：${incident.assignee_username || "未指派"}`, "muted"));
      const canManageIncident = ["operator", "admin"].includes(principal.role);
      if (canManageIncident && !["resolved", "closed"].includes(incident.status)) {
        try {
          const candidates = await api("/api/incident-assignees");
          const severitySelect = document.createElement("select");
          severitySelect.setAttribute("aria-label", "事故严重级别");
          for (const value of ["critical", "high", "medium", "low"]) {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = severityLabels[value];
            severitySelect.append(option);
          }
          severitySelect.value = incident.severity;

          const assigneeSelect = document.createElement("select");
          assigneeSelect.setAttribute("aria-label", "事故负责人");
          const unassigned = document.createElement("option");
          unassigned.value = "";
          unassigned.textContent = "未指派";
          assigneeSelect.append(unassigned);
          if (incident.assignee_id && !candidates.items.some((user) => user.id === incident.assignee_id)) {
            const unavailable = document.createElement("option");
            unavailable.value = incident.assignee_id;
            unavailable.textContent = `${incident.assignee_username || "当前负责人"}（不可指派）`;
            unavailable.disabled = true;
            assigneeSelect.append(unavailable);
          }
          for (const user of candidates.items) {
            const option = document.createElement("option");
            option.value = user.id;
            option.textContent = `${user.username}（${user.role}）`;
            assigneeSelect.append(option);
          }
          assigneeSelect.value = incident.assignee_id || "";
          const initialAssigneeId = incident.assignee_id || "";
          const saveTriage = el("button", "保存分级与负责人");
          saveTriage.style.marginTop = "10px";
          saveTriage.addEventListener("click", async () => {
            saveTriage.disabled = true;
            const body = { severity: severitySelect.value };
            if (assigneeSelect.value !== initialAssigneeId) body.assignee_id = assigneeSelect.value || null;
            try {
              await api(`/api/incidents/${encodeURIComponent(id)}/triage`, { method: "PATCH", body: JSON.stringify(body) });
              await loadIncident(id);
              await loadIncidents();
            } catch (error) {
              showError(pageMessage, error);
              saveTriage.disabled = false;
            }
          });
          triage.append(severitySelect, assigneeSelect, saveTriage);
        } catch (error) {
          showError(pageMessage, error);
        }
      }
      detail.append(triage);

      if (canManageIncident) {
        const lifecycle = document.createElement("section");
        lifecycle.append(el("h3", "事故状态"));
        const transitions = statusTransitions[incident.status] || [];
        if (transitions.length) {
          const statusSelect = document.createElement("select");
          statusSelect.setAttribute("aria-label", "事故状态");
          for (const value of [incident.status, ...transitions]) {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = statusLabels[value] || value;
            statusSelect.append(option);
          }
          const saveStatus = el("button", "更新事故状态");
          saveStatus.style.marginTop = "10px";
          saveStatus.addEventListener("click", async () => {
            if (statusSelect.value === incident.status) return;
            saveStatus.disabled = true;
            try {
              await api(`/api/incidents/${encodeURIComponent(id)}/status`, {
                method: "PATCH",
                body: JSON.stringify({ status: statusSelect.value }),
              });
              await loadIncident(id);
              await loadIncidents();
            } catch (error) {
              showError(pageMessage, error);
              saveStatus.disabled = false;
            }
          });
          lifecycle.append(statusSelect, saveStatus);
        } else {
          lifecycle.append(el("p", "事故已关闭，状态不可再变更。", "muted"));
        }
        detail.append(lifecycle);
      }

      const tasks = document.createElement("section");
      tasks.append(el("h3", "调查任务与证据"));
      if (!incident.tasks.length) tasks.append(el("p", "尚无调查任务。", "muted"));
      for (const task of incident.tasks) {
        const line = document.createElement("div");
        line.className = "task";
        line.append(el("span", `${task.task_type} · ${task.status} · 尝试 ${task.attempt}/${task.max_attempts}`));
        if (["operator", "admin"].includes(principal.role) && task.status === "failed") {
          const retry = el("button", "重试调查");
          retry.addEventListener("click", async () => {
            retry.disabled = true;
            try {
              await api(`/api/tasks/${encodeURIComponent(task.id)}/retry`, { method: "POST" });
              await loadIncident(id);
              await loadIncidents();
            } catch (error) { showError(pageMessage, error); retry.disabled = false; }
          });
          line.append(retry);
        }
        tasks.append(line);
        if (task.error_code) tasks.append(el("p", `错误代码：${task.error_code}`, "muted"));
        if (task.result) {
          const result = task.result;
          if (result.analysis) tasks.append(el("pre", result.analysis));
          tasks.append(el("p", `证据状态：${result.evidence_status || "未标记"}`, "muted"));
          for (const evidence of result.evidence || []) {
            tasks.append(el("pre", JSON.stringify(evidence, null, 2)));
          }
        }
      }
      detail.append(tasks);

      const approvals = document.createElement("section");
      approvals.append(el("h3", "审批"));
      const canOperate = ["operator", "admin"].includes(principal.role);
      const existingActions = new Set(incident.approvals.map((approval) => approval.action_id));
      if (canOperate) {
        for (const [enabled, actionKey, label] of [
          [true, "set-chaos-mode:on", "请求开启演示故障"],
          [false, "set-chaos-mode:off", "请求关闭演示故障"],
        ]) {
          if (!existingActions.has(actionKey)) {
            const request = el("button", label);
            request.className = "primary";
            request.style.margin = "8px 8px 4px 0";
            request.addEventListener("click", async () => {
              request.disabled = true;
              try {
                await api(`/api/incidents/${encodeURIComponent(id)}/approvals`, {
                  method: "POST",
                  body: JSON.stringify({ action: "set-chaos-mode", resource: "order-service", enabled }),
                });
                await loadIncident(id);
              } catch (error) { showError(pageMessage, error); request.disabled = false; }
            });
            approvals.append(request);
          }
        }
      }
      if (!incident.approvals.length) approvals.append(el("p", "暂无审批记录。", "muted"));
      for (const approval of incident.approvals) {
        const row = document.createElement("div");
        row.className = "task";
        row.append(el("span", `${approval.action_id} · ${approval.status} · 申请人 ${approval.requested_by}`));
        const runApprovalAction = async (button, path, body) => {
          button.disabled = true;
          pageMessage.textContent = "";
          try {
            await api(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
            await loadIncident(id);
          } catch (error) { showError(pageMessage, error); button.disabled = false; }
        };
        if (approval.status === "pending" && ["approver", "admin"].includes(principal.role) && approval.requested_by !== principal.user_id) {
          const approve = el("button", "批准");
          approve.addEventListener("click", () => runApprovalAction(approve, `/api/approvals/${encodeURIComponent(approval.id)}/decision`, { decision: "approve" }));
          const reject = el("button", "拒绝");
          reject.style.marginLeft = "6px";
          reject.addEventListener("click", () => runApprovalAction(reject, `/api/approvals/${encodeURIComponent(approval.id)}/decision`, { decision: "reject" }));
          row.append(approve, reject);
        }
        if (approval.status === "pending" && canOperate && (approval.requested_by === principal.user_id || principal.role === "admin")) {
          const cancel = el("button", "取消");
          cancel.addEventListener("click", () => runApprovalAction(cancel, `/api/approvals/${encodeURIComponent(approval.id)}/cancel`));
          row.append(cancel);
        }
        if (approval.status === "approved" && canOperate) {
          const execute = el("button", "执行测试动作");
          execute.className = "primary";
          execute.addEventListener("click", () => runApprovalAction(execute, `/api/approvals/${encodeURIComponent(approval.id)}/execute`));
          row.append(execute);
        }
        approvals.append(row);
      }
      detail.append(approvals);

      const retrospective = await api(`/api/incidents/${encodeURIComponent(id)}/retrospective`);
      const reviewSection = document.createElement("section");
      reviewSection.append(el("h3", "事故复盘"));
      const canReview = ["approver", "admin"].includes(principal.role);
      const reviewState = retrospective.status === "reviewed"
        ? `已审核 · ${retrospective.reviewed_by || "未知审核人"} · ${new Date(retrospective.reviewed_at).toLocaleString()}`
        : `草稿${retrospective.updated_by ? ` · 最近编辑 ${retrospective.updated_by}` : " · 尚未填写"}`;
      reviewSection.append(el("p", reviewState, "muted"));
      const reviewFields = [
        ["影响范围", "impact", retrospective.impact],
        ["根因", "root_cause", retrospective.root_cause],
        ["恢复措施", "resolution", retrospective.resolution],
        ["后续行动（每行一项）", "action_items", retrospective.action_items.join("\n")],
      ];
      const reviewInputs = {};
      for (const [labelText, key, value] of reviewFields) {
        const label = el("label", labelText);
        const input = document.createElement("textarea");
        input.value = value;
        input.readOnly = !canReview;
        input.setAttribute("aria-label", labelText);
        label.append(input);
        reviewSection.append(label);
        reviewInputs[key] = input;
      }
      if (canReview) {
        for (const [label, reviewed] of [["保存草稿", false], ["保存并标记已审核", true]]) {
          const save = el("button", label);
          save.className = reviewed ? "primary" : "";
          save.style.marginRight = "8px";
          save.addEventListener("click", async () => {
            save.disabled = true;
            try {
              await api(`/api/incidents/${encodeURIComponent(id)}/retrospective`, {
                method: "PUT",
                body: JSON.stringify({
                  impact: reviewInputs.impact.value,
                  root_cause: reviewInputs.root_cause.value,
                  resolution: reviewInputs.resolution.value,
                  action_items: reviewInputs.action_items.value.split("\n").map((item) => item.trim()).filter(Boolean),
                  reviewed,
                }),
              });
              await loadIncident(id);
            } catch (error) { showError(pageMessage, error); save.disabled = false; }
          });
          reviewSection.append(save);
        }
      }
      detail.append(reviewSection);

      const timeline = document.createElement("section");
      timeline.append(el("h3", "审计时间线"));
      if (!incident.timeline.length) timeline.append(el("p", "暂无审计事件。", "muted"));
      for (const event of incident.timeline) {
        timeline.append(el("p", `${new Date(event.created_at).toLocaleString()} · ${event.event_type} · ${event.actor || "系统"}`, "muted"));
        if (event.details && Object.keys(event.details).length) timeline.append(el("pre", JSON.stringify(event.details, null, 2)));
      }
      detail.append(timeline);
    } catch (error) {
      detail.replaceChildren(el("p", error.message, "message"));
    }
  }

  async function enterWorkbench() {
    try {
      principal = await api("/auth/me");
      loginPanel.classList.add("hidden");
      workbench.classList.remove("hidden");
      document.getElementById("identity").textContent = `${principal.username} · ${principal.role}`;
      if (principal.role === "admin") {
        userAdmin.classList.remove("hidden");
        await loadUsers();
      } else {
        userAdmin.classList.add("hidden");
      }
      await loadIncidents();
    } catch (error) {
      if (token()) showLogin(error.message);
    }
  }

  document.getElementById("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    button.disabled = true;
    loginMessage.textContent = "正在验证…";
    try {
      const credentials = {
        username: document.getElementById("username").value,
        password: document.getElementById("password").value,
      };
      const result = await api("/auth/login", { method: "POST", body: JSON.stringify(credentials) });
      sessionStorage.setItem(tokenKey, result.access_token);
      document.getElementById("password").value = "";
      await enterWorkbench();
    } catch (error) { showError(loginMessage, error); }
    finally { button.disabled = false; }
  });
  document.getElementById("refresh").addEventListener("click", loadIncidents);
  document.getElementById("refresh-users").addEventListener("click", loadUsers);
  document.getElementById("status-filter").addEventListener("change", loadIncidents);
  oidcLogin.addEventListener("click", () => { window.location.assign("/auth/login"); });
  document.getElementById("logout").addEventListener("click", async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch (error) {
      showError(pageMessage, error);
      return;
    }
    sessionStorage.removeItem(tokenKey);
    principal = null;
    detail.replaceChildren(el("p", "选择一条事故查看调查结果和时间线。", "muted"));
    showLogin();
  });
  fetch("/auth/mode")
    .then((response) => response.json())
    .then((config) => {
      authMode = config.mode;
      if (authMode === "oidc") {
        document.getElementById("local-login-title").textContent = "组织账号登录";
        document.getElementById("local-login-description").textContent = "使用组织身份提供方登录。";
        document.getElementById("login-form").classList.add("hidden");
        oidcLogin.classList.remove("hidden");
      } else {
        fetch("/auth/local-test-defaults", { cache: "no-store" })
          .then((response) => response.ok ? response.json() : null)
          .then((defaults) => {
            if (!defaults || token()) return;
            document.getElementById("username").value = defaults.username;
            document.getElementById("password").value = defaults.password;
          });
      }
      if (token() || authMode === "oidc") enterWorkbench();
    })
    .catch(() => showLogin("无法读取登录配置，请稍后重试。"));
})();
