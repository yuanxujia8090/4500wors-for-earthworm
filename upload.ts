import fs from "node:fs";
import path from "node:path";

import { eq, sql } from "drizzle-orm";
import { db } from "@earthworm/db";
import { course as courseSchema, coursePack, statement as statementSchema } from "@earthworm/schema";

type Statement = typeof statementSchema.$inferInsert;

const COURSE_PACK_TITLE = "四级4500词文章练习 v2";
const COURSE_PACK_DESCRIPTION = "四级4500词文章练习课程包 v2";
const COURSE_PACK_ORDER = 2;
const COURSE_PACK_COVER =
  "https://raw.githubusercontent.com/yuanxujia8090/4500wors-for-earthworm/refs/heads/main/assets/z-image-turbo_00084_.png";
const COURSE_PACK_CREATOR_ID = "1";

(async function () {
  try {
    const coursesDir = path.resolve(__dirname, "data/courses");

    if (!fs.existsSync(coursesDir)) {
      console.error(`错误: 课程目录不存在: ${coursesDir}`);
      process.exit(1);
    }

    const courseFiles = fs
      .readdirSync(coursesDir)
      .filter((f) => f.endsWith(".json"))
      .sort();

    console.log(`找到 ${courseFiles.length} 个课程文件`);

    // Step 1: Check if course pack already exists, reuse it or create new
    const existingPack = await db
      .select()
      .from(coursePack)
      .where(eq(coursePack.title, COURSE_PACK_TITLE))
      .limit(1);

    let coursePackEntity: typeof coursePack.$inferSelect;
    if (existingPack.length > 0) {
      coursePackEntity = existingPack[0];
      console.log(`课程包已存在: id=${coursePackEntity.id} title="${COURSE_PACK_TITLE}"`);
    } else {
      [coursePackEntity] = await db
        .insert(coursePack)
        .values({
          order: COURSE_PACK_ORDER,
          title: COURSE_PACK_TITLE,
          description: COURSE_PACK_DESCRIPTION,
          creatorId: COURSE_PACK_CREATOR_ID,
          shareLevel: "public",
          isFree: true,
          cover: COURSE_PACK_COVER,
        })
        .returning();
      console.log(`创建课程包: id=${coursePackEntity.id} title="${COURSE_PACK_TITLE}"`);
    }

    // Step 2: Create courses (skip if already exists)
    const courseList = await Promise.all(
      courseFiles.map(async (courseFileName, index) => {
        const expectedTitle = `第${courseFileName.replace(".json", "")}课`;
        const existingCourse = await db
          .select()
          .from(courseSchema)
          .where(eq(courseSchema.title, expectedTitle))
          .limit(1);

        let course: typeof courseSchema.$inferSelect;
        if (existingCourse.length > 0) {
          course = existingCourse[0];
          console.log(`课程已存在: id=${course.id} title="${expectedTitle}"`);
        } else {
          [course] = await db
            .insert(courseSchema)
            .values({
              coursePackId: coursePackEntity.id,
              order: index + 1,
              title: expectedTitle,
            })
            .returning({ id: courseSchema.id, order: courseSchema.order, title: courseSchema.title });
          console.log(`创建课程: id=${course.id} order=${course.order} title="${expectedTitle}"`);
        }

        return {
          ...course,
          meta: { courseFileName },
        };
      }),
    );

    // Step 3: Insert statements for each course (delete existing first to ensure consistency)
    await Promise.all(
      courseList.map(async (course) => {
        const { id: courseId, meta } = course;

        const courseDataJsonText = fs.readFileSync(
          path.resolve(coursesDir, meta.courseFileName),
          "utf-8",
        );

        const statementList = JSON.parse(courseDataJsonText) as Statement[];

        if (!Array.isArray(statementList) || statementList.length === 0) {
          console.warn(`警告: ${meta.courseFileName} 不是有效的语句数组，跳过`);
          return;
        }

        // Delete existing statements for this course to avoid duplicates
        await db.delete(statementSchema).where(eq(statementSchema.courseId, courseId));

        // Build statement values with correct order using index
        const statementValues = statementList.map((statement, index) => ({
          ...statement,
          order: index + 1,
          courseId,
        }));

        console.log(`课程 ${meta.courseFileName}: 开始上传 ${statementValues.length} 条语句`);

        // Insert in batches of 100
        const batchSize = 100;
        for (let i = 0; i < statementValues.length; i += batchSize) {
          const batch = statementValues.slice(i, i + batchSize);
          await db.insert(statementSchema).values(batch);
        }

        console.log(`课程 ${meta.courseFileName}: 全部上传成功 (${statementValues.length} 条语句)`);
      }),
    );

    console.log("全部创建完成！");
  } catch (error) {
    console.error("导入失败:", error);
    process.exit(1);
  }
})();
