const assert = require("node:assert/strict");
const test = require("node:test");

process.env.ORDER_ACTION_TOKEN = "local-test-action-token";
process.env.CHAOS_MODE = "off";

const { DemoController, OrdersService } = require("../dist/main.js");

function fixture() {
  const orders = new OrdersService();
  return new DemoController(orders);
}

function actionBody(enabled) {
  return { action: "set-chaos-mode", resource: "order-service", enabled };
}

test("test action rejects missing or incorrect service authentication", () => {
  const controller = fixture();

  assert.throws(
    () => controller.testAction(undefined, "action-1", actionBody(true)),
    (error) => error.getStatus() === 401,
  );
  assert.throws(
    () => controller.testAction("wrong-token", "action-1", actionBody(true)),
    (error) => error.getStatus() === 401,
  );
  assert.deepEqual(controller.testActionState("local-test-action-token"), {
    resource: "order-service",
    chaos_mode: "off",
  });
});

test("test action only accepts the fixed action, resource and boolean parameter", () => {
  const controller = fixture();
  const token = "local-test-action-token";

  for (const body of [
    { action: "restart-host", resource: "order-service", enabled: true },
    { action: "set-chaos-mode", resource: "other-service", enabled: true },
    { action: "set-chaos-mode", resource: "order-service", enabled: "on" },
    { action: "set-chaos-mode", resource: "order-service", enabled: false, command: "rm -rf" },
  ]) {
    assert.throws(
      () => controller.testAction(token, "action-invalid", body),
      (error) => error.getStatus() === 400,
    );
  }
  assert.equal(controller.testActionState(token).chaos_mode, "off");
});

test("test action sets demo chaos state and returns the same result on retry", () => {
  const controller = fixture();
  const token = "local-test-action-token";
  const body = actionBody(true);

  const result = controller.testAction(token, "action-123", body);
  assert.deepEqual(result, {
    accepted: true,
    action_id: "action-123",
    action: "set-chaos-mode",
    resource: "order-service",
    chaos_mode: "on",
    duplicate: false,
  });
  assert.equal(controller.testActionState(token).chaos_mode, "on");
  assert.deepEqual(controller.testAction(token, "action-123", body), {
    ...result,
    duplicate: true,
  });
});

test("test action rejects idempotency key reuse with different parameters", () => {
  const controller = fixture();
  const token = "local-test-action-token";
  controller.testAction(token, "action-456", actionBody(true));

  assert.throws(
    () => controller.testAction(token, "action-456", actionBody(false)),
    (error) => error.getStatus() === 409,
  );
  assert.equal(controller.testActionState(token).chaos_mode, "on");
});
