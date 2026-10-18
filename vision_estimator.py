"""Lightweight pixel-based image features and dimension-based mass estimate.
Not a trained waste recognition model; category labels are nearest-template matches
against three visually reviewed example photographs. Weight requires pile dimensions
for site-specific reliability.
"""
from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError
import numpy as np
import math

# Image descriptors measured from three labeled palm-waste reference photos.
# Feature order: brown, beige, green, woody, luminance, chroma,
# x-edges, y-edges, strong edges, luminance variation.
REFERENCES = [
    ("كرب", [0.3643,0.3940,0.0003,0.3333,0.5786,0.1076,0.1064,0.1016,0.5398,0.4931], {"كرب":0.90,"سعف":0.10}),
    ("مخلفات مختلطة", [0.6943,0.3636,0.0014,0.5296,0.5162,0.1614,0.0632,0.0659,0.3763,0.4787], {"سعف":0.4,"كرب":0.35,"جذع":0.25}),
    ("سعف وكرب", [0.8295,0.3966,0.0035,0.7881,0.4578,0.1708,0.0707,0.0765,0.4023,0.4344], {"سعف":0.60,"كرب":0.40})
]
DENSITY_KG_PER_M3 = {"سعف":100, "كرب":230, "عذق":130, "ليف":100, "جذع":400}
SCALE = np.array([.25,.23,.14,.33,.21,.18,.12,.12,.26,.26],dtype=np.float32)

def analyze_pixels(image_bytes, dimensions=None, material_hint=None):
    try:
        with Image.open(BytesIO(image_bytes)) as original:
            original = ImageOps.exif_transpose(original)
            orig_width, orig_height = original.size
            if orig_width < 40 or orig_height < 40:
                raise ValueError("الصورة صغيرة جدًا للتحليل")
            image = original.convert("RGB").resize((128,128))
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("تعذر قراءة الصورة. استخدم JPG أو PNG أو WEBP.") from exc

    pixels = np.asarray(image, dtype=np.float32)
    red, green, blue = pixels[:,:,0], pixels[:,:,1], pixels[:,:,2]
    mx, mn = np.max(pixels,axis=2), np.min(pixels,axis=2)
    luminance = 0.299*red + 0.587*green + 0.114*blue
    chroma = mx - mn
    brown = (red > green*1.05) & (green > blue*1.06) & (luminance > 40)
    beige = (luminance > 120) & (chroma < 95) & (red > blue*1.10)
    green_mask = (green > red*1.09) & (green > blue*1.09) & (green>50)
    woody = (red>70) & (red>blue*1.22) & (red >= green*.93) & (green >= blue*1.03)
    dx = np.abs(luminance[:,1:]-luminance[:,:-1])
    dy = np.abs(luminance[1:,:]-luminance[:-1,:])
    gradient = np.sqrt((luminance[1:,1:]-luminance[1:,:-1])**2 +
                       (luminance[1:,1:]-luminance[:-1,1:])**2)
    features = np.array([brown.mean(),beige.mean(),green_mask.mean(),woody.mean(),
        luminance.mean()/255,chroma.mean()/255,dx.mean()/255,dy.mean()/255,
        (gradient>28).mean(),luminance.std()/128],dtype=np.float32)

    candidates = []
    for label, prototype, distribution in REFERENCES:
        normalized_error = (features - np.array(prototype,dtype=np.float32))/SCALE
        distance = float(np.sqrt(np.mean(normalized_error**2)))
        candidates.append((distance,label,distribution))
    candidates.sort(key=lambda x:x[0])
    best_distance, best_label, mix = candidates[0]

    # Not proof that the photo contains palm waste: provide low-confidence outcome for
    # images with no recognizable palm-waste color/texture resemblance.
    if (best_distance > 1.20 or (features[0]+features[3]) < .15):
        best_label, mix = "غير محدد", {"سعف":.5,"كرب":.5}
    if material_hint in DENSITY_KG_PER_M3:
        best_label, mix = material_hint, {material_hint:1.0}
    if material_hint == "مخلفات مختلطة":
        best_label, mix = "مخلفات مختلطة", {"سعف":.45,"كرب":.40,"عذق":.15}

    # A physical pile shape factor is used; without on-site dimensions only a
    # REFERENCE SCENARIO is displayed, not a mass measurement from pixels.
    measured = False
    try:
        if isinstance(dimensions, dict):
            length,width,height = (float(dimensions[k]) for k in ("length","width","height"))
            if all(math.isfinite(v) and .1<=v<=40 for v in (length,width,height)):
                measured = True
        if not measured:
            length,width,height = 3.0,2.0,1.0
    except (KeyError,ValueError,TypeError):
        length,width,height = 3.0,2.0,1.0

    packed_volume = length*width*height*.58
    breakdown = []
    total = 0
    for name, proportion in mix.items():
        volume = packed_volume*proportion
        mass = round(volume*DENSITY_KG_PER_M3[name])
        total += mass
        breakdown.append({
          "material":name,
          "estimation_method":"volumetric_pile",
          "estimated_dimensions_or_count": f"{length:g} × {width:g} × {height:g} م؛ شكل الكومة 0.58",
          "estimated_volume_m3":round(volume,2),
          "packing_or_moisture_notes":"كثافة حجمية استرشادية تختلف حسب الرطوبة وطريقة الرص",
          "estimated_weight_kg":mass
        })
    low = round(total*(.60 if measured else .25))
    high = round(total*(1.55 if measured else 3))
    reference = (f"أبعاد أدخلها المستخدم: {length:g} × {width:g} × {height:g} م."
       if measured else f"لا تتوفر أبعاد فعلية؛ الحساب على كومة مرجعية {length:g} × {width:g} × {height:g} م، وليس وزنًا مستخرجًا من الصورة.")
    return {
       "demo_mode":False,
       "analysis_method":"computer_vision_color_texture_template_matching",
       "detected_materials":[best_label],
       "scale_reference_used":reference,
       "analysis_breakdown":breakdown,
       "total_estimated_weight_kg":int(total),
       "confidence_interval_kg":{"min":low,"max":high},
       "confidence_score":"Medium" if best_label != "غير محدد" and best_distance <= 0.75 else "Low",
       "classification_note":"تصنيف بصري أولي من لون وملمس الصورة؛ راجع نوع المخلف قبل اعتماد الطلب.",
       "image_observations":{"width_px":orig_width,"height_px":orig_height,
          "brown_pixels_pct":round(float(features[0])*100,1),
          "woody_color_pixels_pct":round(float(features[3])*100,1),
          "edge_density_pct":round(float(features[8])*100,1),
          "reference_distance":round(best_distance,2)},
       "dimensions_measured":measured
    }
