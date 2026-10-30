// @vitest-environment jsdom
/**
 * EL REFERIDO, DE PUNTA A PUNTA EN EL NAVEGADOR (0070, 05-10-2026):
 *
 *   · el orden del banner: llega con `?ref=`, ve el banner y acepta → en ese
 *     momento se guarda el código de la dirección. Si rechaza, nada;
 *   · si retira el consentimiento, la cookie se va;
 *   · mientras la dirección lleve `?ref=`, los enlaces internos lo llevan.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CookieConsentProvider } from "@/components/legal/cookie-consent-provider";
import { CONSENT_COOKIE, writeConsent } from "@/lib/cookie-consent";
import { COOKIE_REFERIDO, codigoGuardado } from "@/lib/referido";
import { ArrastraElReferido } from "./arrastra-el-referido";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/" }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...resto }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...resto}>
      {children}
    </a>
  ),
}));

function irA(ruta: string) {
  window.history.replaceState(null, "", ruta);
}

function montar(enlaces: React.ReactNode = null) {
  return render(
    <CookieConsentProvider>
      <ArrastraElReferido />
      {enlaces}
    </CookieConsentProvider>,
  );
}

beforeEach(() => {
  push.mockReset();
});

afterEach(() => {
  cleanup();
  document.cookie = `${COOKIE_REFERIDO}=; path=/; max-age=0`;
  document.cookie = `${CONSENT_COOKIE}=; path=/; max-age=0`;
  irA("/");
});

describe("la cookie, en el orden del banner", () => {
  it("llega con ?ref=, ve el banner y ACEPTA: se guarda en ese momento", async () => {
    irA("/?ref=K7QM4P");
    montar();
    expect(codigoGuardado(document.cookie)).toBeNull(); // todavía no ha dicho nada
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Aceptar todas" }));
    });
    expect(codigoGuardado(document.cookie)).toBe("K7QM4P");
  });

  it("si RECHAZA, no se guarda nada", async () => {
    irA("/?ref=K7QM4P");
    montar();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Rechazar todas" }));
    });
    expect(codigoGuardado(document.cookie)).toBeNull();
  });

  it("con el consentimiento ya dado de antes, se guarda al llegar", () => {
    writeConsent({ analiticas: false, marketing: true });
    irA("/signup?ref=K7QM4P");
    montar();
    expect(codigoGuardado(document.cookie)).toBe("K7QM4P");
  });

  it("si RETIRA el consentimiento, la cookie se va", async () => {
    writeConsent({ analiticas: false, marketing: true });
    irA("/?ref=K7QM4P");
    montar();
    expect(codigoGuardado(document.cookie)).toBe("K7QM4P");
    await act(async () => {
      writeConsent({ analiticas: false, marketing: false });
    });
    expect(codigoGuardado(document.cookie)).toBeNull();
  });

  it("una cookie que se quedó sin consentimiento (caducado o de antes) se borra al llegar", () => {
    document.cookie = `${COOKIE_REFERIDO}=VIEJO1; path=/`;
    montar();
    expect(codigoGuardado(document.cookie)).toBeNull();
  });
});

describe("la dirección lo arrastra", () => {
  const enlaces = (
    <>
      <a href="/signup?plan=pro">Empieza</a>
      <a href="https://stripe.com/x">Fuera</a>
      <a href="#precios">Precios</a>
    </>
  );

  it("un enlace interno navega con el código, sin guardar nada", async () => {
    irA("/?ref=K7QM4P");
    montar(enlaces);
    await act(async () => {
      fireEvent.click(screen.getByText("Empieza"));
    });
    expect(push).toHaveBeenCalledWith("/signup?plan=pro&ref=K7QM4P");
    expect(document.cookie).not.toContain(COOKIE_REFERIDO);
  });

  it("para una pestaña nueva, el enlace del DOM ya lo lleva", async () => {
    irA("/?ref=K7QM4P");
    montar(enlaces);
    const a = screen.getByText("Empieza");
    await act(async () => {
      fireEvent.click(a, { ctrlKey: true });
    });
    expect(push).not.toHaveBeenCalled();
    expect(a.getAttribute("href")).toBe("/signup?plan=pro&ref=K7QM4P");
  });

  it("fuera del sitio, un ancla de la página, o sin código en la dirección: no se toca", async () => {
    irA("/?ref=K7QM4P");
    montar(enlaces);
    await act(async () => {
      fireEvent.click(screen.getByText("Fuera"));
      fireEvent.click(screen.getByText("Precios"));
    });
    expect(push).not.toHaveBeenCalled();
    cleanup();
    irA("/");
    montar(enlaces);
    await act(async () => {
      fireEvent.click(screen.getByText("Empieza"));
    });
    expect(push).not.toHaveBeenCalled();
  });
});
