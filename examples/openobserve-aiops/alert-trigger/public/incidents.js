(() => {
  const tokenKey = "holmes-aiops-session";
  const token = () => sessionStorage.getItem(tokenKey);
  const loginPanel = document.getElementById("login-panel");
  const workbench = document.getElementById("workbench");
  const localAccountLabel = document.getElementById("local-account-label");
  const localAccountSelector = document.getElementById("local-account");
  const usernameInput = document.getElementById("username");
  const passwordInput = document.getElementById("password");
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
  let incidentCursor = null;
  let incidentQueryVersion = 0;
  let incidentDetailVersion = 0;
  let selectedIncidentId = null;
  let retrospectiveDirty = false;
  let incidentFilters = null;

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

  function confirmDiscardRetrospective() {
    if (!retrospectiveDirty) return true;
    if (!window.confirm("事故复盘有未保存的修改。继续操作会丢弃这些修改，是否继续？")) return false;
    retrospectiveDirty = false;
    return true;
  }

  function renderAlertSummary(value) {
    const summary = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    const labels = {
      source: "来源",
      alert_severity: "告警严重度",
      alert_summary: "告警摘要",
      alert_description: "告警描述",
      alert_trigger_time_str: "告警开始时间",
      cluster: "集群",
      service: "服务",
      namespace: "命名空间",
      job: "Job",
      instance: "实例",
      err_count: "错误数",
      alert_count: "告警数",
    };
    const section = document.createElement("section");
    section.append(el("h3", "告警信息"));
    const fields = document.createElement("dl");
    fields.className = "alert-summary";
    for (const [key, fieldValue] of Object.entries(summary)) {
      if (["runbook_url", "dashboard_url"].includes(key) || fieldValue == null || typeof fieldValue === "object") continue;
      const row = document.createElement("div");
      row.className = "alert-summary-row";
      row.append(el("dt", labels[key] || key), el("dd", String(fieldValue)));
      fields.append(row);
    }
    if (fields.childElementCount) section.append(fields);

    const links = document.createElement("div");
    links.className = "alert-links";
    for (const [key, label] of [["runbook_url", "打开运行手册"], ["dashboard_url", "打开监控面板"]]) {
      const value = summary[key];
      if (typeof value !== "string") continue;
      try {
        const url = new URL(value);
        if (url.protocol !== "https:" || url.username || url.password || url.search) continue;
        const link = el("a", label);
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        links.append(link);
      } catch {
        continue;
      }
    }
    if (links.childElementCount) section.append(links);
    if (!fields.childElementCount && !links.childElementCount) section.append(el("p", "没有附带告警上下文。", "muted"));
    return section;
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

  async function loadIncidents(append = false) {
    const queryVersion = append ? incidentQueryVersion : ++incidentQueryVersion;
    pageMessage.textContent = "";
    const loadMore = document.getElementById("load-more");
    if (!append) {
      incidentCursor = null;
      loadMore.classList.add("hidden");
      listState.textContent = "正在加载事故…";
      listState.classList.remove("hidden");
      table.classList.add("hidden");
      document.getElementById("incident-rows").replaceChildren();
    }
    loadMore.disabled = true;
    try {
      const params = new URLSearchParams();
      if (!append) {
        incidentFilters = {
          status: document.getElementById("status-filter").value,
          severity: document.getElementById("severity-filter").value,
          search: document.getElementById("incident-search").value.trim(),
          assignedToMe: document.getElementById("assigned-to-me").checked,
        };
      }
      if (incidentFilters.status) params.set("status", incidentFilters.status);
      if (incidentFilters.severity) params.set("severity", incidentFilters.severity);
      if (incidentFilters.assignedToMe) params.set("assigned_to_me", "true");
      if (incidentFilters.search) params.set("search", incidentFilters.search);
      if (append && incidentCursor) params.set("cursor", incidentCursor);
      const result = await api(`/api/incidents${params.size ? `?${params}` : ""}`);
      if (queryVersion !== incidentQueryVersion) return;
      const rows = document.getElementById("incident-rows");
      if (!append && !result.items.length) {
        listState.textContent = "当前没有符合条件的事故。";
        loadMore.classList.add("hidden");
        return;
      }
      for (const incident of result.items) {
        const row = document.createElement("tr");
        row.dataset.id = incident.id;
        const alertCell = document.createElement("td");
        const alertButton = el("button", incident.alert_name, "incident-link");
        alertButton.type = "button";
        alertButton.setAttribute("aria-label", `查看事故 ${incident.alert_name}`);
        alertButton.addEventListener("click", (event) => {
          event.stopPropagation();
          loadIncident(incident.id);
        });
        alertCell.append(alertButton);
        row.append(
          alertCell,
          el("td", incident.resource || "order-service"),
          el("td", statusLabels[incident.status] || incident.status, "status"),
          el("td", severityLabels[incident.severity] || incident.severity),
          el("td", incident.assignee_username || "未指派"),
          el("td", (incident.trace_ids || []).join(", ") || "无 Trace"),
        );
        row.addEventListener("click", () => loadIncident(incident.id));
        rows.append(row);
      }
      incidentCursor = result.next_cursor;
      listState.classList.add("hidden");
      table.classList.remove("hidden");
      loadMore.classList.toggle("hidden", !incidentCursor);
    } catch (error) {
      if (queryVersion !== incidentQueryVersion) return;
      showError(pageMessage, error);
      if (!append) listState.textContent = "事故列表加载失败。请重试。";
    } finally {
      if (queryVersion === incidentQueryVersion) loadMore.disabled = false;
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
    if (!confirmDiscardRetrospective()) return;
    selectedIncidentId = id;
    const detailVersion = ++incidentDetailVersion;
    detail.replaceChildren(el("p", "正在加载事故详情…", "muted"));
    try {
      const incident = await api(`/api/incidents/${encodeURIComponent(id)}`);
      if (detailVersion !== incidentDetailVersion) return;
      detail.replaceChildren();
      detail.append(el("h2", incident.alert_name));
      detail.append(el("p", `${incident.resource || "order-service"} · ${statusLabels[incident.status] || incident.status} · ${new Date(incident.created_at).toLocaleString()} · Trace: ${(incident.trace_ids || []).join(", ") || "无"}`, "muted"));
      detail.append(renderAlertSummary(incident.summary));

      const triage = document.createElement("section");
      triage.append(el("h3", "分级与负责人"));
      triage.append(el("p", `严重度：${severityLabels[incident.severity] || incident.severity} · 负责人：${incident.assignee_username || "未指派"}`, "muted"));
      const canManageIncident = ["operator", "admin"].includes(principal.role);
      if (canManageIncident && !["resolved", "closed"].includes(incident.status)) {
        try {
          const candidates = await api(`/api/incident-assignees?resource=${encodeURIComponent(incident.resource || "order-service")}`);
          if (detailVersion !== incidentDetailVersion) return;
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
            if (!confirmDiscardRetrospective()) return;
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
          if (detailVersion !== incidentDetailVersion) return;
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
            if (!confirmDiscardRetrospective()) return;
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
        const outcomeUnknown = ["holmes_outcome_unknown", "worker_outcome_unknown"].includes(task.error_code)
          || task.result?.possible_duplicate_charge === true;
        if (["operator", "admin"].includes(principal.role) && task.status === "failed") {
          const retry = el("button", "重试调查");
          retry.addEventListener("click", async () => {
            if (outcomeUnknown && !window.confirm("上次模型请求可能已被处理并计费，或调查结果无法确认。重试可能再次产生费用。仍要继续吗？")) return;
            if (!confirmDiscardRetrospective()) return;
            retry.disabled = true;
            try {
              await api(`/api/tasks/${encodeURIComponent(task.id)}/retry`, {
                method: "POST",
                body: JSON.stringify({ acknowledge_possible_duplicate_charge: outcomeUnknown }),
              });
              await loadIncident(id);
              await loadIncidents();
            } catch (error) { showError(pageMessage, error); retry.disabled = false; }
          });
          line.append(retry);
        }
        if (["operator", "admin"].includes(principal.role) && ["queued", "retrying"].includes(task.status)) {
          const cancel = el("button", "取消调查");
          cancel.addEventListener("click", async () => {
            if (!window.confirm("取消尚未开始的调查任务？")) return;
            if (!confirmDiscardRetrospective()) return;
            cancel.disabled = true;
            try {
              await api(`/api/tasks/${encodeURIComponent(task.id)}/cancel`, { method: "POST" });
              await loadIncident(id);
              await loadIncidents();
            } catch (error) { showError(pageMessage, error); cancel.disabled = false; }
          });
          line.append(cancel);
        }
        tasks.append(line);
        if (task.status === "running") {
          tasks.append(el("p", "调查已开始，当前不能中断正在执行的 Holmes 请求。", "muted"));
        }
        if (outcomeUnknown) {
          tasks.append(el("p", "模型请求可能已被处理并计费，或调查结果无法确认；人工重试可能再次产生费用。", "muted"));
        } else if (task.error_code) {
          tasks.append(el("p", `错误代码：${task.error_code}`, "muted"));
        }
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
              if (!confirmDiscardRetrospective()) return;
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
        const requester = approval.requested_by_username || approval.requested_by;
        const reviewer = approval.reviewed_by_username || approval.reviewed_by;
        const reviewerLabel = reviewer ? ` · 审核人 ${reviewer}` : "";
        const executionLabel = approval.execution_status ? ` · 执行 ${approval.execution_status}${approval.execution_error ? `（${approval.execution_error}）` : ""}` : "";
        row.append(el("span", `${approval.action_id} · ${approval.status}${executionLabel} · 申请人 ${requester}${reviewerLabel}`));
        const runApprovalAction = async (button, path, body) => {
          if (!confirmDiscardRetrospective()) return;
          button.disabled = true;
          pageMessage.textContent = "";
          try {
            await api(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
            await loadIncident(id);
          } catch (error) {
            showError(pageMessage, error);
            button.disabled = false;
            await loadIncident(id);
          }
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
        if (approval.status === "approved" && canOperate && !["succeeded", "rolled_back", "failed"].includes(approval.execution_status)) {
          const execute = el("button", approval.execution_status ? "恢复/重试执行" : "执行测试动作");
          execute.className = "primary";
          execute.addEventListener("click", () => runApprovalAction(execute, `/api/approvals/${encodeURIComponent(approval.id)}/execute`));
          row.append(execute);
        }
        approvals.append(row);
      }
      detail.append(approvals);

      const retrospective = await api(`/api/incidents/${encodeURIComponent(id)}/retrospective`);
      if (detailVersion !== incidentDetailVersion) return;
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
        ["后续行动（最多 20 项，每项不超过 500 字）", "action_items", retrospective.action_items.join("\n")],
      ];
      const reviewInputs = {};
      for (const [labelText, key, value] of reviewFields) {
        const label = el("label", labelText);
        const input = document.createElement("textarea");
        input.value = value;
        input.maxLength = key === "action_items" ? 10019 : 5000;
        input.readOnly = !canReview;
        input.addEventListener("input", () => { retrospectiveDirty = true; });
        input.setAttribute("aria-label", labelText);
        label.append(input);
        reviewSection.append(label);
        reviewInputs[key] = input;
      }
      if (canReview) {
        const saveButtons = [];
        for (const [label, reviewed] of [["保存草稿", false], ["保存并标记已审核", true]]) {
          const save = el("button", label);
          saveButtons.push(save);
          save.className = reviewed ? "primary" : "";
          save.style.marginRight = "8px";
          save.addEventListener("click", async () => {
            save.disabled = true;
            for (const input of Object.values(reviewInputs)) input.disabled = true;
            for (const button of saveButtons) button.disabled = true;
            try {
              const actionItems = reviewInputs.action_items.value.split("\n").map((item) => item.trim()).filter(Boolean);
              if (actionItems.length > 20 || actionItems.some((item) => item.length > 500)) {
                throw new Error("后续行动最多填写 20 项，每项最多 500 字。请修改后再保存。");
              }
              await api(`/api/incidents/${encodeURIComponent(id)}/retrospective`, {
                method: "PUT",
                body: JSON.stringify({
                  impact: reviewInputs.impact.value,
                  root_cause: reviewInputs.root_cause.value,
                  resolution: reviewInputs.resolution.value,
                  action_items: actionItems,
                  reviewed,
                }),
              });
              retrospectiveDirty = false;
              await loadIncident(id);
            } catch (error) {
              showError(pageMessage, error);
              for (const input of Object.values(reviewInputs)) input.disabled = false;
              for (const button of saveButtons) button.disabled = false;
            }
          });
          reviewSection.append(save);
        }
      }
      detail.append(reviewSection);

      const timeline = document.createElement("section");
      timeline.append(el("h3", "审计时间线"));
      const timelineEntries = document.createElement("div");
      const appendTimelineEvents = (events, older = false) => {
        const fragment = document.createDocumentFragment();
        for (const event of events) {
          const entry = document.createElement("div");
          entry.append(el("p", `${new Date(event.created_at).toLocaleString()} · ${event.event_type} · ${event.actor || "系统"}`, "muted"));
          if (event.details && Object.keys(event.details).length) entry.append(el("pre", JSON.stringify(event.details, null, 2)));
          fragment.append(entry);
        }
        if (older) timelineEntries.prepend(fragment);
        else timelineEntries.append(fragment);
      };
      appendTimelineEvents(incident.timeline);
      if (!incident.timeline.length) timelineEntries.append(el("p", "暂无审计事件。", "muted"));
      timeline.append(timelineEntries);
      let timelineCursor = incident.timeline_next_cursor;
      if (timelineCursor) {
        const loadOlder = el("button", "加载更早审计事件");
        loadOlder.addEventListener("click", async () => {
          loadOlder.disabled = true;
          try {
            const page = await api(`/api/incidents/${encodeURIComponent(id)}/timeline?limit=50&cursor=${encodeURIComponent(timelineCursor)}`);
            appendTimelineEvents(page.items, true);
            timelineCursor = page.next_cursor;
            if (!timelineCursor) loadOlder.remove();
            else loadOlder.disabled = false;
          } catch (error) {
            timelineEntries.prepend(el("p", error.message, "message"));
            loadOlder.disabled = false;
          }
        });
        timeline.append(loadOlder);
      }
      detail.append(timeline);
    } catch (error) {
      if (detailVersion !== incidentDetailVersion) return;
      detail.replaceChildren(el("p", error.message, "message"));
    }
  }

  async function enterWorkbench() {
    try {
      principal = await api("/auth/me");
      loginPanel.classList.add("hidden");
      workbench.classList.remove("hidden");
      const resourceScopes = Array.isArray(principal.resource_scopes) ? principal.resource_scopes : [];
      const scopeLabel = resourceScopes.includes("*") ? "全部服务" : resourceScopes.join("、") || "无服务范围";
      document.getElementById("identity").textContent = `${principal.username} · ${principal.role} · 服务范围：${scopeLabel}`;
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
  document.getElementById("refresh").addEventListener("click", async () => {
    const detailId = selectedIncidentId;
    await Promise.all([loadIncidents(), detailId ? loadIncident(detailId) : Promise.resolve()]);
  });
  document.getElementById("apply-filters").addEventListener("click", () => loadIncidents());
  document.getElementById("load-more").addEventListener("click", () => loadIncidents(true));
  document.getElementById("incident-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadIncidents();
  });
  document.getElementById("refresh-users").addEventListener("click", loadUsers);
  document.getElementById("status-filter").addEventListener("change", () => loadIncidents());
  document.getElementById("severity-filter").addEventListener("change", () => loadIncidents());
  document.getElementById("assigned-to-me").addEventListener("change", () => loadIncidents());
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
            if (!defaults || !Array.isArray(defaults.accounts)) return;
            const accounts = defaults.accounts.filter((account) =>
              account && typeof account.username === "string" &&
              typeof account.password === "string" && typeof account.role === "string"
            );
            if (!accounts.length) return;
            localAccountSelector.replaceChildren();
            for (const account of accounts) {
              const option = document.createElement("option");
              option.value = account.username;
              option.textContent = `${account.username} (${account.role})`;
              localAccountSelector.append(option);
            }
            localAccountLabel.classList.remove("hidden");
            localAccountSelector.classList.remove("hidden");
            const fillCredentials = () => {
              const selected = accounts.find((account) => account.username === localAccountSelector.value);
              if (!selected) return;
              usernameInput.value = selected.username;
              passwordInput.value = selected.password;
            };
            localAccountSelector.addEventListener("change", fillCredentials);
            localAccountSelector.value = accounts.some((account) => account.username === defaults.default_username)
              ? defaults.default_username
              : accounts[0].username;
            fillCredentials();
          });
      }
      if (token() || authMode === "oidc") enterWorkbench();
    })
    .catch(() => showLogin("无法读取登录配置，请稍后重试。"));
})();
