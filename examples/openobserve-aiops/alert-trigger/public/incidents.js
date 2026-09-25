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
  let principal = null;

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (token()) headers.set("Authorization", `Bearer ${token()}`);
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    if (response.status === 401 && token()) {
      sessionStorage.removeItem(tokenKey);
      showLogin("登录已过期，请重新登录。");
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
        row.append(el("td", incident.alert_name), el("td", incident.status, "status"), el("td", (incident.trace_ids || []).join(", ") || "无 Trace"));
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

  async function loadIncident(id) {
    detail.replaceChildren(el("p", "正在加载事故详情…", "muted"));
    try {
      const incident = await api(`/api/incidents/${encodeURIComponent(id)}`);
      detail.replaceChildren();
      detail.append(el("h2", incident.alert_name));
      detail.append(el("p", `${incident.status} · ${new Date(incident.created_at).toLocaleString()} · Trace: ${(incident.trace_ids || []).join(", ") || "无"}`, "muted"));
      const summary = document.createElement("section");
      summary.append(el("h3", "告警信息"), el("pre", JSON.stringify(incident.summary || {}, null, 2)));
      detail.append(summary);

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
  document.getElementById("status-filter").addEventListener("change", loadIncidents);
  document.getElementById("logout").addEventListener("click", () => {
    sessionStorage.removeItem(tokenKey);
    principal = null;
    detail.replaceChildren(el("p", "选择一条事故查看调查结果和时间线。", "muted"));
    showLogin();
  });
  if (token()) enterWorkbench();
})();
