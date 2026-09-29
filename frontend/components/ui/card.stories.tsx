import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const meta: Meta = { title: "Atoms/Card" };
export default meta;

export const Default: StoryObj = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Latest deploy</CardTitle>
        <CardDescription>checkout · production</CardDescription>
        <CardAction>
          <Button size="sm" variant="outline">
            View
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="text-sm">
        Image pull denied for registry.example.com/checkout.
      </CardContent>
      <CardFooter className="text-muted-foreground text-xs">2 minutes ago</CardFooter>
    </Card>
  ),
};
