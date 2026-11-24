import {
  CreateStartUpPageContainer,
  MenuContainerProperty,
  MenuItemProperty,
  TextContainerProperty,
  TextContainerUpgrade,
  type EvenAppBridge,
} from '@evenrealities/even_hub_sdk';
import type { HudCard } from './types';

export const MENU_ITEM_PULL_NOTION = 1;
export const MENU_ITEM_NEXT_HOLDING = 2;
export const MENU_ITEM_REFRESH = 3;

const CONTAINER_HEADER_ID = 1;
const CONTAINER_BODY_ID = 2;
const CONTAINER_FOOTER_ID = 3;

/**
 * Renders Cockpit onto the Even G2 576x288 monochrome green display using
 * 3 fixed text containers initialized once at startup and updated flicker-free
 * via `textContainerUpgrade`.
 */
export class G2HudRenderer {
  private initialized = false;
  private lastCard: HudCard | null = null;

  constructor(private readonly bridge: EvenAppBridge | null) {}

  async init(initialCard: HudCard): Promise<void> {
    const safe = sanitizeCard(initialCard);
    this.lastCard = safe;
    this.mirrorToCompanionDom(safe);

    if (!this.bridge) {
      this.initialized = true;
      return;
    }

    const header = new TextContainerProperty({
      xPosition: 8,
      yPosition: 4,
      width: 560,
      height: 44,
      paddingLength: 2,
      containerID: CONTAINER_HEADER_ID,
      containerName: 'header',
      content: safe.header,
      textColor: 2,
      isEventCapture: 0,
    });

    const body = new TextContainerProperty({
      xPosition: 8,
      yPosition: 52,
      width: 560,
      height: 184,
      borderWidth: 1,
      borderColor: 4,
      borderRadius: 4,
      paddingLength: 6,
      containerID: CONTAINER_BODY_ID,
      containerName: 'body',
      content: safe.body,
      textColor: 4,
      isEventCapture: 1,
    });

    const footer = new TextContainerProperty({
      xPosition: 8,
      yPosition: 240,
      width: 560,
      height: 44,
      paddingLength: 2,
      containerID: CONTAINER_FOOTER_ID,
      containerName: 'footer',
      content: safe.footer,
      textColor: 1,
      isEventCapture: 0,
    });

    const menu = new MenuContainerProperty({
      menuItems: [
        new MenuItemProperty({ itemID: MENU_ITEM_PULL_NOTION, itemName: 'Pull Notion questions' }),
        new MenuItemProperty({ itemID: MENU_ITEM_NEXT_HOLDING, itemName: 'Next holding' }),
        new MenuItemProperty({ itemID: MENU_ITEM_REFRESH, itemName: 'Refresh deck' }),
      ],
    });

    await this.bridge.createStartUpPageContainer(
      new CreateStartUpPageContainer({
        containerTotalNum: 3,
        textObject: [header, body, footer],
        menuObject: menu,
      }),
    );

    this.initialized = true;
  }

  async render(card: HudCard): Promise<void> {
    const safe = sanitizeCard(card);
    if (!this.initialized) {
      await this.init(safe);
      return;
    }

    this.mirrorToCompanionDom(safe);
    if (!this.bridge) {
      this.lastCard = safe;
      return;
    }

    const prev = this.lastCard;
    if (!prev || prev.header !== safe.header) {
      await this.bridge.textContainerUpgrade(
        new TextContainerUpgrade({
          containerID: CONTAINER_HEADER_ID,
          containerName: 'header',
          contentOffset: 0,
          contentLength: safe.header.length,
          content: safe.header,
          textColor: 2,
        }),
      );
    }
    if (!prev || prev.body !== safe.body) {
      await this.bridge.textContainerUpgrade(
        new TextContainerUpgrade({
          containerID: CONTAINER_BODY_ID,
          containerName: 'body',
          contentOffset: 0,
          contentLength: safe.body.length,
          content: safe.body,
          textColor: 4,
        }),
      );
    }
    if (!prev || prev.footer !== safe.footer) {
      await this.bridge.textContainerUpgrade(
        new TextContainerUpgrade({
          containerID: CONTAINER_FOOTER_ID,
          containerName: 'footer',
          contentOffset: 0,
          contentLength: safe.footer.length,
          content: safe.footer,
          textColor: 1,
        }),
      );
    }

    this.lastCard = safe;
  }

  private mirrorToCompanionDom(card: HudCard): void {
    const h = document.getElementById('hud-header');
    const b = document.getElementById('hud-body');
    const f = document.getElementById('hud-footer');
    if (h) h.textContent = card.header;
    if (b) b.textContent = card.body;
    if (f) f.textContent = card.footer;
  }
}

function sanitizeLine(value: string, maxLen: number): string {
  // Strip emoji / surrogate pairs unsupported by G2 LVGL fixed font
  const cleaned = value.replace(/[\u{1F000}-\u{1FFFF}]/gu, '').trim();
  if (cleaned.length <= maxLen) return cleaned || ' ';
  return `${cleaned.slice(0, maxLen - 1).trimEnd()}…`;
}

function sanitizeCard(card: HudCard): HudCard {
  return {
    header: sanitizeLine(card.header, 58),
    body: sanitizeLine(card.body, 240),
    footer: sanitizeLine(card.footer, 58),
  };
}
