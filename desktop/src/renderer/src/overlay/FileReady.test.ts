import { describe, expect, it } from "vitest";
import { cleanBody, kindOf, readyTitle, sizeText } from "./fileReadyText";

describe("the new-file card's words", () => {
  it("names what was made", () => {
    expect(readyTitle([String.raw`C:\Users\Mina\Desktop\Offer letter.pdf`])).toBe("Your PDF is ready");
    expect(readyTitle(["a (page 1).pdf", "a (page 2).pdf", "a (page 3).pdf"])).toBe("3 PDFs are ready");
    expect(readyTitle(["budget (edited by Pyxi).xlsx"])).toBe("Your spreadsheet is ready");
    expect(readyTitle(["a.pdf", "b.docx"])).toBe("2 files are ready");
    expect(readyTitle(["IMG_2043.jpg", "IMG_2044.jpg"])).toBe("2 images are ready");
  });

  it("gives each kind of file its own badge", () => {
    expect(kindOf("x.PDF").badge).toBe("PDF");
    expect(kindOf("x.docx").badge).toBe("DOC");
    expect(kindOf("x.pptx").badge).toBe("PPT");
    expect(kindOf("x.heic").badge).toBe("IMG");
    expect(kindOf("archive.zip").badge).toBe("ZIP");
  });

  it("leaves out the folder path the card already shows", () => {
    expect(cleanBody(String.raw`Made Offer letter.pdf (2 pages). It's in C:\Users\Mina\Desktop.`)).toBe(
      "Made Offer letter.pdf (2 pages).",
    );
    expect(
      cleanBody(String.raw`Split lease.pdf into 4 PDFs. They're in C:\Users\Mina\Downloads\lease (split).`),
    ).toBe("Split lease.pdf into 4 PDFs.");
    expect(cleanBody("Added a Total row.")).toBe("Added a Total row.");
  });

  it("says sizes the way people do", () => {
    expect(sizeText(900)).toBe("900 bytes");
    expect(sizeText(248_000)).toBe("242 KB");
    expect(sizeText(4_400_000)).toBe("4.2 MB");
  });
});
