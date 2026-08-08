(function() {
  var defaults = {
    sync: "namar_test.delivery_components.api.sync_delivery_component_packages",
    get: "namar_test.delivery_components.api.get_delivery_component_packages",
    markReady: "namar_test.delivery_components.api.mark_delivery_component_package_ready",
    markEvent: "namar_test.delivery_components.api.mark_delivery_component_package_event",
    fulfillment: "namar_test.delivery_components.api.get_material_request_fulfillment_readiness",
    resolve: "namar_test.delivery_components.api.resolve_delivery_tracking_code"
  };
  window.NAMAR_DELIVERY_COMPONENT_API = Object.assign(
    {},
    defaults,
    window.NAMAR_DELIVERY_COMPONENT_API || {}
  );
})();
