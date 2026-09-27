import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
} from "./alert-dialog";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./dialog";

/**
 * Dialogs overflowed their frame when a title held an unbroken string
 * (#2125): "Discard failed deploy <40-char SHA>?" pushed the textarea and
 * footer past the border. The content is a CSS grid, and a grid track's
 * minimum is its min-content width, so one long word widened the track past
 * the container.
 *
 * jsdom does no layout, so these pin the two rules that keep it from
 * happening: a column that may shrink below its content, and text that may
 * wrap anywhere.
 */
const SHA = "1112015d9730847df4441f2ebccb87717adb88d3";

describe("dialog primitives stay inside their frame (#2125)", () => {
  it("AlertDialog content lets its column shrink, and title and description wrap anywhere", () => {
    render(
      <AlertDialog open>
        <AlertDialogContent data-testid="content">
          <AlertDialogTitle>Discard failed deploy {SHA}?</AlertDialogTitle>
          <AlertDialogDescription>{SHA}</AlertDialogDescription>
        </AlertDialogContent>
      </AlertDialog>
    );
    expect(screen.getByTestId("content").className).toContain("grid-cols-[minmax(0,1fr)]");
    expect(screen.getByText(`Discard failed deploy ${SHA}?`).className).toContain(
      "[overflow-wrap:anywhere]"
    );
    expect(screen.getByText(SHA).className).toContain("[overflow-wrap:anywhere]");
  });

  it("Dialog content lets its column shrink, and title and description wrap anywhere", () => {
    render(
      <Dialog open>
        <DialogContent data-testid="content">
          <DialogTitle>{SHA}</DialogTitle>
          <DialogDescription>arn:aws:iam::123456789012:role/{SHA}</DialogDescription>
        </DialogContent>
      </Dialog>
    );
    expect(screen.getByTestId("content").className).toContain("grid-cols-[minmax(0,1fr)]");
    expect(screen.getByText(SHA).className).toContain("[overflow-wrap:anywhere]");
    expect(screen.getByText(`arn:aws:iam::123456789012:role/${SHA}`).className).toContain(
      "[overflow-wrap:anywhere]"
    );
  });
});
