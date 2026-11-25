import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js";
import { WorkplaceConfigurator3D as BaseConfigurator } from "./workplace-configurator-3d-v106.js";
import { buildWorkshopModel, inferWorkshopModel } from "./workplace-3d-models-v84.js";
import { correctLastBatchProduct } from "./workplace-last-batch-photo-v1.js";

function disposeObject(object) {
  object.traverse((child) => {
    child.geometry?.dispose?.();
    (Array.isArray(child.material) ? child.material : [child.material])
      .filter(Boolean)
      .forEach((entry) => entry.dispose?.());
  });
}

export class WorkplaceConfigurator3D extends BaseConfigurator {
  sync(products) {
    const expected = new Map(products.map((item) => [item.instanceId, item]));
    for (const [id, entry] of this.items) {
      if (!expected.has(id)) {
        this.scene.remove(entry.group);
        if (entry.outline) {
          this.scene.remove(entry.outline);
          entry.outline.geometry?.dispose?.();
          entry.outline.material?.dispose?.();
        }
        disposeObject(entry.group);
        this.items.delete(id);
        if (this.selectedId === id) this.selectedId = null;
      }
    }
    let addedCorner = false;
    let lastAddedId = null;
    products.forEach((item, index) => {
      if (this.items.has(item.instanceId)) return;
      const product = correctLastBatchProduct(item.product);
      const group = buildWorkshopModel(THREE, product);
      group.userData.instanceId = item.instanceId;
      group.userData.variantId = String(product.variantId);
      this.scene.add(group);
      const outline = new THREE.BoxHelper(group, 2413167);
      outline.visible = false;
      outline.material.depthTest = false;
      outline.material.transparent = true;
      outline.material.opacity = 0.95;
      outline.renderOrder = 20;
      this.scene.add(outline);
      const entry = {
        id: item.instanceId,
        product,
        group,
        outline,
        spec: inferWorkshopModel(product),
        blocked: false,
        aligned: false,
        snapPartnerId: null,
      };
      this.items.set(item.instanceId, entry);
      this.autoPlace(entry, index);
      lastAddedId = item.instanceId;
      if (entry.spec.corner) addedCorner = true;
    });
    if (addedCorner) this.reflowCornerLayers();
    if (lastAddedId) this.selectedId = lastAddedId;
    this.validateAll();
    this.renderOnce();
  }
}
