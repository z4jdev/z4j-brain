import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { SchedulePublic } from "@/lib/api-types";

export function useSchedules(slug: string) {
  return useQuery<SchedulePublic[]>({
    queryKey: ["schedules", slug],
    queryFn: () => api.get<SchedulePublic[]>(`/projects/${slug}/schedules`),
    enabled: !!slug,
    refetchInterval: 30_000,
  });
}

export function useToggleSchedule(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      scheduleId,
      enabled,
    }: {
      scheduleId: string;
      enabled: boolean;
    }) =>
      api.post<SchedulePublic>(
        `/projects/${slug}/schedules/${scheduleId}/${enabled ? "enable" : "disable"}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules", slug] }),
  });
}

export function useTriggerSchedule(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scheduleId: string) =>
      api.post<SchedulePublic>(
        `/projects/${slug}/schedules/${scheduleId}/trigger`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules", slug] }),
  });
}
