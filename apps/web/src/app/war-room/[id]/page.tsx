import { WarRoom } from "@/components/war-room/WarRoom";

export default async function WarRoomPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <WarRoom simulationId={id} />;
}
