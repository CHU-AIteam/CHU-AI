(() => {
  const CHARACTER_BOUNDS = {
    left: 186,
    top: 88,
    right: 1030,
    bottom: 1152,
  };
  const VALID_EMOTIONS = new Set([
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprised",
    "thinking",
    "confused",
  ]);
  const INK = 0x062875;
  const EYE_DARK = 0x03236f;
  const EYE_MID = 0x0749b6;
  const EYE_LIGHT = 0x2e78e7;
  const SCREEN_WHITE = 0xffffff;
  const MOUTH_LIGHT = 0x55a2f3;
  const EFFECT_BLUE = 0x3b86dc;
  const BLUSH = 0x8b68d6;
  const CHARACTER_RIGHT_ARM_MIRROR_ROTATION = 1.06;
  const IDLE_SEQUENCE = [
    "glance",
    "still",
    "shift",
    "still",
    "wander",
    "settle",
    "still",
    "wave",
    "still",
  ];
  const IDLE_DURATIONS = {
    still: 4.2,
    glance: 3.5,
    shift: 3.4,
    wander: 5.4,
    wave: 3.5,
    settle: 2.6,
  };

  function damp(current, target, speed, deltaSeconds) {
    const amount = 1 - Math.exp(-speed * deltaSeconds);
    return current + (target - current) * amount;
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function lerp(start, end, amount) {
    return start + (end - start) * amount;
  }

  function smoothStep(value) {
    const normalized = clamp(value, 0, 1);
    return normalized * normalized * (3 - 2 * normalized);
  }

  function smootherStep(value) {
    const normalized = clamp(value, 0, 1);
    return normalized * normalized * normalized * (normalized * (normalized * 6 - 15) + 10);
  }

  async function loadTexture(url) {
    if (typeof window.PIXI.Texture.fromURL === "function") {
      return window.PIXI.Texture.fromURL(url);
    }

    const texture = window.PIXI.Texture.from(url);
    if (texture.baseTexture.valid) {
      return texture;
    }
    return new Promise((resolve, reject) => {
      texture.baseTexture.once("loaded", () => resolve(texture));
      texture.baseTexture.once("error", reject);
    });
  }

  class KomoPuppet {
    static async create(options) {
      const puppet = new KomoPuppet(options);
      await puppet.initialize();
      return puppet;
    }

    constructor({ canvas, stage, assetUrl, faceAssetUrl, badgeAssetUrl }) {
      this.canvas = canvas;
      this.stageElement = stage;
      this.assetUrl = assetUrl;
      this.faceAssetUrl = faceAssetUrl;
      this.badgeAssetUrl = badgeAssetUrl;
      this.app = null;
      this.texture = null;
      this.faceTexture = null;
      this.badgeTexture = null;
      this.root = null;
      this.characterRig = null;
      this.headRig = null;
      this.antennaRig = null;
      this.bodyRig = null;
      this.leftArmRig = null;
      this.rightArmRig = null;
      this.leftLegRig = null;
      this.rightLegRig = null;
      this.faceGraphics = null;
      this.emotion = "neutral";
      this.speaking = false;
      this.elapsed = 0;
      this.mouthOpen = 0;
      this.blinking = false;
      this.nextBlinkAt = 1.7;
      this.blinkEndAt = 0;
      this.faceRenderKey = "";
      this.emotionPulse = 0;
      this.waveStartedAt = 0;
      this.waveUntil = 0;
      this.pointerX = 0;
      this.pointerY = 0;
      this.pointerTargetX = 0;
      this.pointerTargetY = 0;
      this.antennaVelocity = 0;
      this.active = true;
      this.behaviorBlend = 0;
      this.behaviorGazeX = 0;
      this.behaviorGazeY = 0;
      this.stageOffsetX = 0;
      this.idleSequenceIndex = -1;
      this.idleAction = {
        name: "still",
        startedAt: 0,
        duration: 4.8,
        direction: 1,
        originX: 0,
        targetX: 0,
      };
      this.reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
      this.handlePointerMove = this.handlePointerMove.bind(this);
      this.handlePointerLeave = this.handlePointerLeave.bind(this);
      this.handlePointerDown = this.handlePointerDown.bind(this);
      this.update = this.update.bind(this);
    }

    async initialize() {
      const width = Math.max(this.stageElement.clientWidth, 360);
      const height = Math.max(this.stageElement.clientHeight, 480);
      this.app = new window.PIXI.Application({
        view: this.canvas,
        width,
        height,
        transparent: true,
        backgroundAlpha: 0,
        antialias: true,
        autoDensity: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
      });
      [this.texture, this.faceTexture, this.badgeTexture] = await Promise.all([
        loadTexture(this.assetUrl),
        loadTexture(this.faceAssetUrl),
        loadTexture(this.badgeAssetUrl),
      ]);
      this.buildRig();
      this.app.stage.addChild(this.root);
      this.app.ticker.add(this.update);
      this.canvas.addEventListener("pointermove", this.handlePointerMove, { passive: true });
      this.canvas.addEventListener("pointerleave", this.handlePointerLeave, { passive: true });
      this.canvas.addEventListener("pointerdown", this.handlePointerDown, { passive: true });
      this.resize();
      this.refreshFace(true);
    }

    createMaskedPart(texture, drawMask, pivotX, pivotY) {
      const rig = new window.PIXI.Container();
      const sprite = new window.PIXI.Sprite(texture);
      const mask = new window.PIXI.Graphics();
      mask.beginFill(0xffffff);
      drawMask(mask);
      mask.endFill();
      sprite.mask = mask;
      rig.addChild(sprite, mask);
      rig.pivot.set(pivotX, pivotY);
      rig.position.set(pivotX, pivotY);
      return rig;
    }

    buildRig() {
      this.root = new window.PIXI.Container();
      this.characterRig = new window.PIXI.Container();

      this.bodyRig = new window.PIXI.Container();
      this.bodyRig.pivot.set(627, 755);
      this.bodyRig.position.set(627, 755);

      this.leftLegRig = this.createMaskedPart(
        this.texture,
        (mask) => {
          mask.drawPolygon([
            418, 1010,
            596, 1010,
            600, 1064,
            585, 1125,
            550, 1144,
            465, 1144,
            425, 1118,
          ]);
        },
        536,
        1025,
      );
      this.rightLegRig = this.createMaskedPart(
        this.texture,
        (mask) => {
          mask.drawPolygon([
            655, 1010,
            835, 1010,
            830, 1118,
            790, 1144,
            705, 1144,
            670, 1125,
            652, 1064,
          ]);
        },
        716,
        1025,
      );

      this.leftArmRig = this.createMaskedPart(
        this.texture,
        (mask) => {
          mask.drawPolygon([
            440, 758,
            468, 779,
            454, 846,
            420, 925,
            401, 970,
            386, 1005,
            325, 1011,
            286, 980,
            288, 940,
            313, 895,
            350, 832,
            407, 776,
          ]);
        },
        449,
        770,
      );
      // Screen-left is Komo's anatomical right arm.
      this.leftArmRig.scale.x = -1;
      this.leftArmRig.rotation = CHARACTER_RIGHT_ARM_MIRROR_ROTATION;

      this.rightArmRig = this.createMaskedPart(
        this.texture,
        (mask) => {
          mask.drawPolygon([
            786, 779,
            814, 758,
            847, 776,
            904, 832,
            941, 895,
            982, 928,
            996, 965,
            990, 995,
            968, 1020,
            934, 1034,
            891, 1028,
            861, 1012,
            846, 982,
            853, 970,
            834, 925,
            800, 846,
          ]);
        },
        805,
        770,
      );

      const torsoRig = this.createMaskedPart(
        this.texture,
        (mask) => {
          mask.drawPolygon([
            430, 735,
            824, 735,
            838, 810,
            837, 982,
            824, 1036,
            430, 1036,
            417, 982,
            416, 810,
          ]);
        },
        627,
        755,
      );

      const badgeBacking = new window.PIXI.Graphics();
      badgeBacking.beginFill(0x0d55c7);
      badgeBacking.drawCircle(735, 858, 44);
      badgeBacking.endFill();

      const badgeSprite = new window.PIXI.Sprite(this.badgeTexture);
      badgeSprite.anchor.set(0.5);
      badgeSprite.position.set(735, 858);
      badgeSprite.width = 144;
      badgeSprite.height = 144;

      this.bodyRig.addChild(
        this.leftLegRig,
        this.rightLegRig,
        this.leftArmRig,
        this.rightArmRig,
        torsoRig,
        badgeBacking,
        badgeSprite,
      );

      this.headRig = new window.PIXI.Container();
      this.headRig.pivot.set(627, 742);
      this.headRig.position.set(627, 742);

      this.antennaRig = this.createMaskedPart(
        this.texture,
        (mask) => {
          mask.drawRect(392, 82, 472, 203);
        },
        627,
        286,
      );

      const televisionRig = this.createMaskedPart(
        this.texture,
        (mask) => {
          mask.drawRoundedRect(282, 275, 695, 472, 126);
        },
        627,
        742,
      );

      const blankScreenRig = this.createMaskedPart(
        this.faceTexture,
        (mask) => {
          mask.drawRoundedRect(372, 365, 475, 332, 88);
        },
        627,
        530,
      );

      this.faceGraphics = new window.PIXI.Graphics();
      this.headRig.addChild(
        this.antennaRig,
        televisionRig,
        blankScreenRig,
        this.faceGraphics,
      );
      this.characterRig.addChild(this.bodyRig, this.headRig);
      this.root.addChild(this.characterRig);
    }

    drawOpenEye(graphics, centerX, centerY, scale, gazeX, gazeY) {
      const eyeWidth = 49 * scale;
      const eyeHeight = 66 * scale;
      graphics.beginFill(SCREEN_WHITE, 0.98);
      graphics.drawEllipse(centerX, centerY, eyeWidth, eyeHeight);
      graphics.endFill();

      const pupilX = centerX + gazeX * 5;
      const pupilY = centerY + gazeY * 4;
      graphics.beginFill(EYE_DARK);
      graphics.drawEllipse(pupilX, pupilY + 3, 32 * scale, 53 * scale);
      graphics.endFill();
      graphics.beginFill(EYE_MID);
      graphics.drawEllipse(pupilX, pupilY + 13, 22 * scale, 34 * scale);
      graphics.endFill();
      graphics.beginFill(EYE_LIGHT, 0.72);
      graphics.drawEllipse(pupilX + 5, pupilY + 21, 10 * scale, 15 * scale);
      graphics.endFill();
      graphics.beginFill(SCREEN_WHITE, 0.98);
      graphics.drawEllipse(pupilX - 11, pupilY - 24, 12 * scale, 18 * scale);
      graphics.endFill();
    }

    drawEyes(graphics, eyesClosed, gazeX, gazeY) {
      const leftX = 505;
      const rightX = 718;
      const eyeY = 526;

      if (eyesClosed) {
        graphics.lineStyle(12, INK, 1);
        if (this.emotion === "happy") {
          graphics.moveTo(457, 532);
          graphics.bezierCurveTo(478, 503, 526, 503, 550, 532);
          graphics.moveTo(673, 532);
          graphics.bezierCurveTo(695, 503, 742, 503, 766, 532);
        } else {
          graphics.moveTo(458, 526);
          graphics.bezierCurveTo(482, 539, 527, 539, 551, 526);
          graphics.moveTo(672, 526);
          graphics.bezierCurveTo(696, 539, 741, 539, 765, 526);
        }
        return;
      }

      let scale = 1;
      if (this.emotion === "surprised") {
        scale = 1.08;
      } else if (this.emotion === "angry") {
        scale = 0.88;
      } else if (this.emotion === "sad") {
        gazeY += 0.45;
      } else if (this.emotion === "thinking") {
        gazeX -= 0.42;
      }

      this.drawOpenEye(graphics, leftX, eyeY, scale, gazeX, gazeY);
      this.drawOpenEye(graphics, rightX, eyeY, scale, gazeX, gazeY);
    }

    drawEyebrows(graphics) {
      graphics.lineStyle(10, INK, 0.92);
      if (this.emotion === "angry") {
        graphics.moveTo(459, 430);
        graphics.lineTo(548, 460);
        graphics.moveTo(674, 460);
        graphics.lineTo(763, 430);
        return;
      }
      if (this.emotion === "sad") {
        graphics.moveTo(459, 455);
        graphics.lineTo(548, 426);
        graphics.moveTo(674, 426);
        graphics.lineTo(763, 455);
        return;
      }
      if (this.emotion === "thinking") {
        graphics.moveTo(459, 438);
        graphics.lineTo(548, 425);
        graphics.moveTo(674, 440);
        graphics.lineTo(763, 440);
        return;
      }
      if (this.emotion === "confused") {
        graphics.moveTo(459, 426);
        graphics.lineTo(548, 450);
        graphics.moveTo(674, 445);
        graphics.lineTo(763, 425);
        return;
      }
      if (this.emotion === "surprised") {
        graphics.moveTo(459, 418);
        graphics.bezierCurveTo(482, 397, 525, 397, 548, 418);
        graphics.moveTo(674, 418);
        graphics.bezierCurveTo(697, 397, 740, 397, 763, 418);
        return;
      }

      graphics.moveTo(467, 432);
      graphics.bezierCurveTo(488, 416, 523, 416, 543, 432);
      graphics.moveTo(680, 432);
      graphics.bezierCurveTo(701, 416, 736, 416, 756, 432);
    }

    drawSmile(graphics, width = 68, depth = 28) {
      graphics.lineStyle(11, INK, 1);
      graphics.moveTo(610 - width / 2, 603);
      graphics.bezierCurveTo(575, 648 + depth * 0.25, 646, 661 + depth * 0.25, 610 + width / 2, 603);
    }

    drawMouth(graphics, openness) {
      if (this.speaking) {
        const width = 28 + openness * 19;
        const height = 9 + openness * 35;
        graphics.beginFill(EYE_DARK);
        graphics.drawEllipse(610, 614, width, height);
        graphics.endFill();
        if (openness > 0.38) {
          graphics.beginFill(MOUTH_LIGHT, 0.96);
          graphics.drawEllipse(610, 628, width * 0.68, height * 0.35);
          graphics.endFill();
        }
        return;
      }

      if (this.emotion === "happy") {
        this.drawSmile(graphics, 88, 18);
        return;
      }

      graphics.lineStyle(11, INK, 1);
      if (this.emotion === "sad") {
        graphics.moveTo(562, 637);
        graphics.bezierCurveTo(582, 598, 639, 598, 660, 637);
        return;
      }
      if (this.emotion === "angry") {
        graphics.moveTo(571, 622);
        graphics.lineTo(650, 610);
        return;
      }
      if (this.emotion === "surprised") {
        graphics.lineStyle(0);
        graphics.beginFill(EYE_DARK);
        graphics.drawEllipse(610, 615, 23, 32);
        graphics.endFill();
        return;
      }
      if (this.emotion === "thinking") {
        graphics.moveTo(582, 615);
        graphics.bezierCurveTo(598, 625, 624, 625, 642, 612);
        return;
      }
      if (this.emotion === "confused") {
        graphics.moveTo(561, 618);
        graphics.bezierCurveTo(577, 600, 594, 634, 611, 616);
        graphics.bezierCurveTo(628, 600, 644, 632, 662, 613);
        return;
      }
      this.drawSmile(graphics);
    }

    drawEmotionEffects(graphics) {
      if (this.emotion === "happy") {
        graphics.beginFill(BLUSH, 0.2);
        graphics.drawEllipse(430, 598, 38, 18);
        graphics.drawEllipse(790, 598, 38, 18);
        graphics.endFill();
      }
      if (this.emotion === "sad") {
        graphics.beginFill(EFFECT_BLUE, 0.92);
        graphics.drawCircle(754, 583, 10);
        graphics.drawPolygon([744, 584, 764, 584, 754, 614]);
        graphics.endFill();
      }
      if (this.emotion === "confused") {
        graphics.beginFill(EFFECT_BLUE, 0.84);
        graphics.drawCircle(783, 449, 8);
        graphics.drawPolygon([775, 450, 791, 450, 783, 474]);
        graphics.endFill();
      }
    }

    refreshFace(force = false) {
      if (!this.faceGraphics) {
        return;
      }
      const transitionEyes = this.emotion === "happy" && this.emotionPulse > 0.36;
      const eyesClosed = this.blinking || transitionEyes;
      const mouthStep = Math.round(this.mouthOpen * 12);
      const pointerActivity = clamp(
        Math.abs(this.pointerTargetX) + Math.abs(this.pointerTargetY),
        0,
        1,
      );
      const idleGazeWeight = (1 - pointerActivity) * (this.reduceMotion ? 0.35 : 1);
      const idleGazeX =
        (
          Math.sin(this.elapsed * 0.43)
          + Math.sin(this.elapsed * 0.17 + 1.4) * 0.55
        )
        * 0.34
        * idleGazeWeight;
      const idleGazeY =
        (
          Math.sin(this.elapsed * 0.31 + 0.8)
          + Math.sin(this.elapsed * 0.19 + 2.1) * 0.35
        )
        * 0.2
        * idleGazeWeight;
      const gazeX =
        Math.round(
          clamp(this.pointerX + idleGazeX + this.behaviorGazeX, -1, 1) * 4,
        ) / 4;
      const gazeY =
        Math.round(
          clamp(this.pointerY + idleGazeY + this.behaviorGazeY, -1, 1) * 4,
        ) / 4;
      const key = `${this.emotion}:${eyesClosed}:${this.speaking}:${mouthStep}:${gazeX}:${gazeY}`;
      if (!force && key === this.faceRenderKey) {
        return;
      }
      this.faceRenderKey = key;
      this.faceGraphics.clear();
      this.drawEyes(this.faceGraphics, eyesClosed, gazeX, gazeY);
      this.drawEyebrows(this.faceGraphics);
      this.drawMouth(this.faceGraphics, this.mouthOpen);
      this.drawEmotionEffects(this.faceGraphics);
    }

    updateBlink() {
      if (!this.blinking && this.elapsed >= this.nextBlinkAt) {
        this.blinking = true;
        this.blinkEndAt = this.elapsed + 0.13;
        this.refreshFace(true);
      } else if (this.blinking && this.elapsed >= this.blinkEndAt) {
        this.blinking = false;
        this.nextBlinkAt = this.elapsed + 2.5 + Math.random() * 2.8;
        this.refreshFace(true);
      }
    }

    advanceIdleAction() {
      if (this.idleAction.name === "wander") {
        this.stageOffsetX = this.idleAction.targetX;
      }

      this.idleSequenceIndex = (this.idleSequenceIndex + 1) % IDLE_SEQUENCE.length;
      const name = IDLE_SEQUENCE[this.idleSequenceIndex];
      const baseDuration = IDLE_DURATIONS[name] || 3.5;
      const durationVariation =
        Math.sin((this.idleSequenceIndex + 1) * 1.9 + this.elapsed * 0.07) * 0.35;
      const direction =
        Math.sin(this.elapsed * 0.37 + this.idleSequenceIndex * 1.7) >= 0 ? 1 : -1;
      let targetX = this.stageOffsetX;

      if (name === "wander") {
        if (Math.abs(this.stageOffsetX) < 8) {
          targetX = direction * 28;
        } else {
          targetX = -Math.sign(this.stageOffsetX) * 20;
        }
      }

      this.idleAction = {
        name,
        startedAt: this.elapsed,
        duration: Math.max(baseDuration + durationVariation, 1.8),
        direction,
        originX: this.stageOffsetX,
        targetX,
      };
    }

    getIdlePose() {
      const action = this.idleAction;
      const progress = clamp(
        (this.elapsed - action.startedAt) / Math.max(action.duration, 0.01),
        0,
        1,
      );
      const transitionWindow = 0.18;
      const envelope = smoothStep(
        Math.min(
          clamp(progress / transitionWindow, 0, 1),
          clamp((1 - progress) / transitionWindow, 0, 1),
        ),
      );
      const direction = action.direction;
      const pose = {
        characterX: this.stageOffsetX,
        headX: 0,
        headY: 0,
        headRotation: 0,
        bodyX: 0,
        bodyY: 0,
        bodyRotation: 0,
        bodyScaleX: 1,
        bodyScaleY: 1,
        leftArmDelta: 0,
        rightArmDelta: 0,
        leftLegRotation: 0,
        rightLegRotation: 0,
        antennaRotation: 0,
        gazeX: 0,
        gazeY: 0,
      };

      if (action.name === "glance") {
        pose.gazeX = direction * 0.72 * envelope;
        pose.gazeY = -0.12 * envelope;
        pose.headX = direction * 6 * envelope;
        pose.headY = -1.5 * envelope;
        pose.headRotation = direction * 0.052 * envelope;
        pose.bodyRotation = -direction * 0.009 * envelope;
        pose.leftArmDelta = -direction * 0.018 * envelope;
        pose.rightArmDelta = direction * 0.018 * envelope;
        pose.antennaRotation = -direction * 0.035 * envelope;
      } else if (action.name === "shift") {
        const weight = Math.sin(progress * Math.PI) * envelope;
        pose.bodyX = direction * 9 * weight;
        pose.bodyY = 1.5 * weight;
        pose.bodyRotation = direction * 0.024 * weight;
        pose.headX = direction * 4 * weight;
        pose.headRotation = -direction * 0.032 * weight;
        pose.leftArmDelta = -direction * 0.04 * weight;
        pose.rightArmDelta = -direction * 0.04 * weight;
        pose.leftLegRotation = direction * 0.035 * weight;
        pose.rightLegRotation = direction * 0.018 * weight;
        pose.gazeX = direction * 0.22 * weight;
        pose.antennaRotation = -direction * 0.04 * weight;
      } else if (action.name === "wander") {
        const travel = smootherStep(progress);
        const gaitEnvelope = Math.sin(progress * Math.PI);
        const stepPhase = progress * Math.PI * 8;
        const step = Math.sin(stepPhase) * gaitEnvelope;
        const lift = Math.abs(Math.cos(stepPhase)) * gaitEnvelope;
        pose.characterX = lerp(action.originX, action.targetX, travel);
        pose.bodyY = -lift * 3.6;
        pose.bodyRotation = direction * 0.016 * gaitEnvelope;
        pose.headY = lift * 1.2;
        pose.headRotation = -direction * 0.018 * gaitEnvelope - step * 0.007;
        pose.leftArmDelta = -step * 0.085;
        pose.rightArmDelta = step * 0.085;
        pose.leftLegRotation = step * 0.095;
        pose.rightLegRotation = -step * 0.095;
        pose.gazeX = direction * 0.24 * gaitEnvelope;
        pose.antennaRotation = -step * 0.045;
      } else if (action.name === "wave") {
        const wave = Math.sin(progress * Math.PI * 6) * envelope;
        pose.bodyX = 5 * envelope;
        pose.bodyY = -2 * envelope;
        pose.bodyRotation = -0.026 * envelope;
        pose.headX = 3 * envelope;
        pose.headY = -2 * envelope;
        pose.headRotation = 0.045 * envelope - wave * 0.006;
        pose.leftArmDelta = 1.54 * envelope + wave * 0.12;
        pose.rightArmDelta = -0.04 * envelope;
        pose.leftLegRotation = -0.025 * envelope;
        pose.rightLegRotation = 0.04 * envelope;
        pose.gazeX = -0.12 * envelope;
        pose.antennaRotation = wave * 0.055;
      } else if (action.name === "settle") {
        const rebound = Math.sin(progress * Math.PI * 2) * envelope;
        const compression = Math.sin(progress * Math.PI) * envelope;
        pose.bodyY = compression * 2.5;
        pose.bodyScaleX = 1 + compression * 0.008;
        pose.bodyScaleY = 1 - compression * 0.012;
        pose.headY = -rebound * 1.8;
        pose.headRotation = rebound * 0.012;
        pose.leftArmDelta = rebound * 0.018;
        pose.rightArmDelta = -rebound * 0.018;
        pose.antennaRotation = -rebound * 0.07;
      }

      return pose;
    }

    updateMotion(deltaSeconds) {
      this.pointerX = damp(this.pointerX, this.pointerTargetX, 7, deltaSeconds);
      this.pointerY = damp(this.pointerY, this.pointerTargetY, 7, deltaSeconds);
      this.emotionPulse *= Math.exp(-3.2 * deltaSeconds);

      const motionScale = this.reduceMotion ? 0.18 : 1;
      const pointerActivity = clamp(
        Math.abs(this.pointerTargetX) + Math.abs(this.pointerTargetY),
        0,
        1,
      );
      const settledEmotion =
        this.emotion === "neutral"
        || (this.emotion === "happy" && this.emotionPulse < 0.05);
      const canIdle =
        settledEmotion
        && !this.speaking
        && this.elapsed >= this.waveUntil
        && pointerActivity < 0.82;

      if (
        canIdle
        && this.elapsed >= this.idleAction.startedAt + this.idleAction.duration
      ) {
        this.advanceIdleAction();
      }

      this.behaviorBlend = damp(
        this.behaviorBlend,
        canIdle ? 1 : 0,
        canIdle ? 2.2 : 5.5,
        deltaSeconds,
      );

      const idlePose = this.getIdlePose();
      const behaviorWeight =
        this.behaviorBlend * motionScale * (1 - pointerActivity * 0.72);
      this.behaviorGazeX = damp(
        this.behaviorGazeX,
        idlePose.gazeX * behaviorWeight,
        4,
        deltaSeconds,
      );
      this.behaviorGazeY = damp(
        this.behaviorGazeY,
        idlePose.gazeY * behaviorWeight,
        4,
        deltaSeconds,
      );

      const slowSway = Math.sin(this.elapsed * 0.48);
      const counterSway = Math.sin(this.elapsed * 0.93 + 0.7);
      const quietWeight = (1 - pointerActivity * 0.7) * motionScale;
      const sharedSwayX = (slowSway * 1.15 + counterSway * 0.35) * quietWeight;
      const breath = Math.sin(this.elapsed * 1.42) * 0.0065 * motionScale;
      const sharedBobY =
        (
          Math.sin(this.elapsed * 1.42) * 0.75
          + Math.sin(this.elapsed * 0.52 + 1.1) * 0.35
        )
        * motionScale;

      let characterOffsetX = lerp(
        this.stageOffsetX,
        idlePose.characterX,
        behaviorWeight,
      );
      let headRotation =
        this.pointerX * 0.024
        - slowSway * 0.005 * quietWeight
        + idlePose.headRotation * behaviorWeight;
      let headOffsetX =
        this.pointerX * 7
        + sharedSwayX
        + idlePose.headX * behaviorWeight;
      let headOffsetY =
        this.pointerY * 2
        + idlePose.headY * behaviorWeight;
      let headScale = 1;
      let bodyOffsetX = sharedSwayX + idlePose.bodyX * behaviorWeight;
      let bodyOffsetY = idlePose.bodyY * behaviorWeight;
      let bodyRotation =
        slowSway * 0.003 * quietWeight
        + idlePose.bodyRotation * behaviorWeight;
      let sharedPoseY = 0;
      let bodyScaleX =
        (1 - breath * 0.28)
        * lerp(1, idlePose.bodyScaleX, behaviorWeight);
      let bodyScaleY =
        (1 + breath)
        * lerp(1, idlePose.bodyScaleY, behaviorWeight);
      let leftArmRotation =
        CHARACTER_RIGHT_ARM_MIRROR_ROTATION
        + Math.sin(this.elapsed * 0.76) * 0.009 * quietWeight
        + idlePose.leftArmDelta * behaviorWeight;
      let rightArmRotation =
        -Math.sin(this.elapsed * 0.76 + 0.35) * 0.009 * quietWeight
        + idlePose.rightArmDelta * behaviorWeight;
      let leftLegRotation =
        Math.sin(this.elapsed * 0.58 + 0.3) * 0.004 * quietWeight
        + idlePose.leftLegRotation * behaviorWeight;
      let rightLegRotation =
        -Math.sin(this.elapsed * 0.58 + 0.3) * 0.004 * quietWeight
        + idlePose.rightLegRotation * behaviorWeight;
      let antennaTarget =
        -headRotation * 0.72
        + Math.sin(this.elapsed * 0.84 + 0.9) * 0.012 * quietWeight
        + idlePose.antennaRotation * behaviorWeight;

      if (this.emotion === "happy") {
        const response = this.emotionPulse;
        headRotation += Math.sin(this.elapsed * 3.1) * 0.018 * response;
        sharedPoseY -= Math.sin(response * Math.PI) * 4;
        leftArmRotation += 0.035 + response * 0.04;
        rightArmRotation -= 0.035 + response * 0.04;
        antennaTarget += Math.sin(this.elapsed * 4.8) * 0.035 * response;
      } else if (this.emotion === "sad") {
        headRotation -= 0.035;
        headOffsetY += 5;
        bodyOffsetY += 2.5;
        bodyScaleY -= 0.01;
        leftArmRotation -= 0.08;
        rightArmRotation += 0.08;
        antennaTarget -= 0.05;
      } else if (this.emotion === "angry") {
        const response = this.emotionPulse;
        headOffsetX += Math.sin(this.elapsed * 18) * 1.6 * response;
        headRotation += Math.sin(this.elapsed * 14) * 0.006 * response;
        leftArmRotation += 0.15;
        rightArmRotation -= 0.15;
        bodyOffsetY += 2;
      } else if (this.emotion === "surprised") {
        headScale += 0.02 + this.emotionPulse * 0.045;
        sharedPoseY -= 2 + this.emotionPulse * 4;
        leftArmRotation += 0.42 + this.emotionPulse * 0.5;
        rightArmRotation -= 0.42 + this.emotionPulse * 0.5;
        antennaTarget += 0.075;
      } else if (this.emotion === "thinking") {
        const thought = Math.sin(this.elapsed * 1.15);
        headRotation -= 0.055 + thought * 0.01;
        headOffsetX -= 4;
        bodyOffsetX -= 3;
        bodyOffsetY += 1;
        rightArmRotation = -0.2 + thought * 0.018;
        leftArmRotation += 0.045;
        leftLegRotation -= 0.018;
        rightLegRotation += 0.025;
        bodyRotation += 0.014;
        antennaTarget -= 0.03;
      } else if (this.emotion === "confused") {
        const questionTilt = Math.sin(this.elapsed * 1.45);
        headRotation += questionTilt * 0.045;
        leftArmRotation += 0.14 + questionTilt * 0.04;
        rightArmRotation -= 0.14 + questionTilt * 0.04;
        bodyRotation -= questionTilt * 0.008;
        antennaTarget += questionTilt * 0.045;
      }

      if (this.elapsed < this.waveUntil) {
        const waveDuration = Math.max(this.waveUntil - this.waveStartedAt, 0.01);
        const waveProgress = clamp(
          (this.elapsed - this.waveStartedAt) / waveDuration,
          0,
          1,
        );
        const waveEnvelope = smoothStep(
          Math.min(
            clamp(waveProgress / 0.2, 0, 1),
            clamp((1 - waveProgress) / 0.22, 0, 1),
          ),
        );
        const wave = Math.sin(waveProgress * Math.PI * 6) * waveEnvelope;
        const raisedArmRotation =
          CHARACTER_RIGHT_ARM_MIRROR_ROTATION
          + 1.58
          + wave * 0.12 * motionScale;

        leftArmRotation = lerp(leftArmRotation, raisedArmRotation, waveEnvelope);
        rightArmRotation -= 0.035 * waveEnvelope;
        bodyOffsetX += 6 * waveEnvelope;
        bodyOffsetY -= 2 * waveEnvelope;
        bodyRotation -= 0.028 * waveEnvelope;
        headOffsetX += 3 * waveEnvelope;
        headOffsetY -= 2 * waveEnvelope;
        headRotation += 0.045 * waveEnvelope - wave * 0.006;
        leftLegRotation -= 0.025 * waveEnvelope;
        rightLegRotation += 0.04 * waveEnvelope;
        antennaTarget += wave * 0.06;
      }

      headScale += this.emotionPulse * 0.014;

      this.characterRig.position.x = damp(
        this.characterRig.position.x,
        characterOffsetX,
        3.2,
        deltaSeconds,
      );
      this.headRig.rotation = damp(this.headRig.rotation, headRotation, 7, deltaSeconds);
      this.headRig.position.x = damp(this.headRig.position.x, 627 + headOffsetX, 8, deltaSeconds);
      this.headRig.position.y = damp(
        this.headRig.position.y,
        742 + sharedBobY + sharedPoseY + headOffsetY,
        8,
        deltaSeconds,
      );
      this.headRig.scale.x = damp(this.headRig.scale.x, headScale, 8, deltaSeconds);
      this.headRig.scale.y = damp(this.headRig.scale.y, headScale, 8, deltaSeconds);

      this.bodyRig.position.x = damp(
        this.bodyRig.position.x,
        627 + bodyOffsetX,
        7,
        deltaSeconds,
      );
      this.bodyRig.position.y = damp(
        this.bodyRig.position.y,
        755 + sharedBobY + sharedPoseY + bodyOffsetY,
        8,
        deltaSeconds,
      );
      this.bodyRig.rotation = damp(this.bodyRig.rotation, bodyRotation, 7, deltaSeconds);
      this.bodyRig.scale.x = damp(this.bodyRig.scale.x, bodyScaleX, 6, deltaSeconds);
      this.bodyRig.scale.y = damp(this.bodyRig.scale.y, bodyScaleY, 6, deltaSeconds);
      this.leftArmRig.rotation = damp(this.leftArmRig.rotation, leftArmRotation, 8, deltaSeconds);
      this.rightArmRig.rotation = damp(this.rightArmRig.rotation, rightArmRotation, 8, deltaSeconds);
      this.leftLegRig.rotation = damp(this.leftLegRig.rotation, leftLegRotation, 7, deltaSeconds);
      this.rightLegRig.rotation = damp(this.rightLegRig.rotation, rightLegRotation, 7, deltaSeconds);

      this.antennaVelocity += (antennaTarget - this.antennaRig.rotation) * 18 * deltaSeconds;
      this.antennaVelocity *= Math.exp(-6.2 * deltaSeconds);
      this.antennaRig.rotation += this.antennaVelocity * deltaSeconds;
    }
    update(delta) {
      if (!this.active) {
        return;
      }
      const deltaSeconds = clamp(delta / 60, 0, 0.05);
      this.elapsed += deltaSeconds;
      this.updateBlink();

      const mouthTarget = this.speaking
        ? 0.18 + Math.abs(Math.sin(this.elapsed * 10.8)) * 0.78
        : 0;
      this.mouthOpen = damp(this.mouthOpen, mouthTarget, 24, deltaSeconds);
      this.updateMotion(deltaSeconds);
      this.refreshFace();
    }

    setEmotion(value, { animate = true } = {}) {
      const nextEmotion = VALID_EMOTIONS.has(value) ? value : "neutral";
      const changed = this.emotion !== nextEmotion;
      this.emotion = nextEmotion;
      if (animate && changed) {
        this.emotionPulse = 1;
        if (nextEmotion === "happy") {
          this.startWave();
        }
      }
      this.refreshFace(true);
    }

    setSpeaking(value) {
      this.speaking = Boolean(value);
      if (!this.speaking) {
        this.mouthOpen = 0;
      }
      this.refreshFace(true);
    }

    setActive(value) {
      const nextActive = Boolean(value);
      if (this.active === nextActive) {
        return;
      }
      this.active = nextActive;
      this.pointerTargetX = 0;
      this.pointerTargetY = 0;
      if (nextActive) {
        this.idleAction = {
          name: "still",
          startedAt: this.elapsed,
          duration: 3.8,
          direction: 1,
          originX: this.stageOffsetX,
          targetX: this.stageOffsetX,
        };
        this.refreshFace(true);
      }
    }

    resize() {
      if (!this.app || !this.root) {
        return;
      }
      const width = this.stageElement.clientWidth;
      const height = this.stageElement.clientHeight;
      if (width < 2 || height < 2) {
        return;
      }

      this.app.renderer.resize(width, height);
      const boundsWidth = CHARACTER_BOUNDS.right - CHARACTER_BOUNDS.left;
      const boundsHeight = CHARACTER_BOUNDS.bottom - CHARACTER_BOUNDS.top;
      const scale = Math.min(width / boundsWidth, height / boundsHeight) * 0.94;
      const centerX = (CHARACTER_BOUNDS.left + CHARACTER_BOUNDS.right) / 2;
      this.root.scale.set(scale);
      this.root.position.set(
        width / 2 - centerX * scale,
        height * 0.985 - CHARACTER_BOUNDS.bottom * scale,
      );
    }

    handlePointerMove(event) {
      const rect = this.canvas.getBoundingClientRect();
      this.pointerTargetX = clamp(((event.clientX - rect.left) / rect.width - 0.5) * 2, -1, 1);
      this.pointerTargetY = clamp(((event.clientY - rect.top) / rect.height - 0.5) * 2, -1, 1);
    }

    handlePointerLeave() {
      this.pointerTargetX = 0;
      this.pointerTargetY = 0;
    }

    handlePointerDown() {
      this.emotionPulse = 1;
      this.startWave();
    }

    startWave(duration = 2.4) {
      this.waveStartedAt = this.elapsed;
      this.waveUntil = this.elapsed + duration;
    }
  }

  window.KomoPuppet = KomoPuppet;
})();
