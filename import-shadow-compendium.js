// Shadow Vault - Importer 4.0 (Robust Sequential & Dual Image Schema)
const packName = "shadow-vault.maps";
const pack = game.packs.get(packName);

if (!pack) {
  ui.notifications.error(`Compendium ${packName} not found!`);
  return;
}

if (pack.locked) {
  ui.notifications.error("Please unlock the 'Shadow Maps' compendium first!");
  return;
}

// 1. Wipe out existing scenes
const existingIds = pack.index.map(idx => idx._id);
if (existingIds.length > 0) {
  ui.notifications.info(`Clearing ${existingIds.length} old scenes...`);
  await Scene.deleteDocuments(existingIds, { pack: packName });
}

// 2. Fetch the JSON metadata file
const response = await fetch("modules/shadow-vault/amber_map_index.json");
if (!response.ok) {
  ui.notifications.error("Could not find amber_map_index.json");
  return;
}
const mapData = await response.json();
ui.notifications.info(`Loaded ${mapData.length} maps from JSON. Beginning import...`);

// 3. Process and create in small, rock-solid batches of 20
const BATCH_SIZE = 20;
let successCount = 0;

for (let i = 0; i < mapData.length; i += BATCH_SIZE) {
  const slice = mapData.slice(i, i + BATCH_SIZE);
  const docsToCreate = [];

  for (const map of slice) {
    const imgPath = `modules/shadow-vault/images/${map.filename}`;
    const w = Number(map.width) || 4000;
    const h = Number(map.height) || 3000;

    docsToCreate.push({
      name: map.title || map.filename,
      width: w,
      height: h,
      
      // Temporary thumbnail so the compendium UI isn't blank
      thumb: imgPath,

      // V14 Level Architecture
      initialLevel: "defaultLevel0000",
      levels: [
        {
          _id: "defaultLevel0000",
          name: "Level",
          elevation: { bottom: 0, top: 20 },
          background: {
            color: "#999999",
            src: imgPath,
            tint: "#ffffff",
            alphaThreshold: 0.75
          },
          foreground: { src: null, tint: "#ffffff", alphaThreshold: 0.75 },
          fog: { src: null },
          textures: {
            anchorX: 0.5, anchorY: 0.5, offsetX: 0, offsetY: 0,
            fit: "fill", scaleX: 1, scaleY: 1, rotation: 0
          },
          visibility: { levels: [] },
          sort: 0,
          flags: {}
        }
      ],

      // Explicit grid definition
      grid: { size: 100, type: 1 },

      // Golden DNA
      tokenVision: true,
      fog: { mode: 2, colors: { explored: null, unexplored: null } },
      environment: {
        globalLight: {
          enabled: true, alpha: 0.5, bright: false, color: null,
          coloration: 1, luminosity: 0, saturation: 0, contrast: 0,
          shadows: 0, darkness: { min: 0, max: 1 }
        },
        darknessLevel: 0, darknessLock: false, cycle: true,
        base: { hue: 0, intensity: 0, luminosity: 0, saturation: 0, shadows: 0 },
        dark: { hue: 0.7138888888888889, intensity: 0, luminosity: -0.25, saturation: 0, shadows: 0 }
      },

      flags: {
        "shadow-vault": {
          filename: map.filename,
          era: map.era,
          environment: map.environment,
          atmosphere: map.atmosphere,
          order_chaos_scale: map.order_chaos_scale,
          features: map.features,
          shadow_shift_cues: map.shadow_shift_cues,
          summary: map.summary
        }
      }
    });
  }

  try {
    await Scene.createDocuments(docsToCreate, { pack: packName, keepId: false });
    successCount += docsToCreate.length;
    if (successCount % 100 === 0 || successCount === mapData.length) {
      ui.notifications.info(`Imported ${successCount} / ${mapData.length} scenes...`);
      console.log(`[Shadow Vault] Progress: ${successCount}/${mapData.length}`);
    }
  } catch (err) {
    console.error(`[Shadow Vault] Error in batch ${i} to ${i + BATCH_SIZE}:`, err);
  }
}

// 4. Force compendium pack index refresh
await pack.getIndex();
ui.notifications.info(`Done! Successfully imported ${successCount} scenes into Shadow Vault.`);
